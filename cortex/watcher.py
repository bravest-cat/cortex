"""
Cortex Automated Incremental File Watcher.
Monitors project directories using watchdog, debouncing file modification events
and incrementally synchronizing Qdrant vectors and FalkorDB symbol graphs in near real-time.
Leverages ChunkEmbeddingCache to make single-file re-indexing sub-second.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
from dotenv import load_dotenv
from llama_index.core.schema import Document
from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client import models as qmodels
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from cortex.cache import ChunkEmbeddingCache, IngestCache
from cortex.chunking import SmartCodeNodeParser
from cortex.ingest import DEFAULT_EXCLUDE, SUPPORTED_EXT
from cortex.symbols import SymbolGraphManager
from cortex.chunking.ast_splitter import ASTCodeSplitter, LANG_MAP
from cortex.viking import VikingChunker

load_dotenv()

EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "http://localhost:8000/v1")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "mlx-community--Qwen3-Embedding-8B-4bit-DWQ")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "cortex_codebase")
DEFAULT_DEBOUNCE = float(os.getenv("CORTEX_WATCH_DEBOUNCE", "2.0"))
WATCHER_STATUS_FILE = Path(os.getenv("CORTEX_WATCHER_STATUS", str(Path.home() / ".cortex" / "watcher_status.json")))


def write_status(
    running: bool,
    pid: Optional[int] = None,
    root: Optional[str] = None,
    collection: Optional[str] = None,
    debounce_sec: Optional[float] = None,
    total_processed: int = 0,
    last_event_time: Optional[float] = None,
    last_synced_file: Optional[str] = None,
    pending_count: int = 0,
):
    """Persists watcher lifecycle and telemetry to status file."""
    try:
        WATCHER_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "running": running,
            "pid": pid or os.getpid(),
            "root": root,
            "collection": collection,
            "debounce_sec": debounce_sec,
            "total_processed": total_processed,
            "last_event_time": last_event_time,
            "last_synced_file": last_synced_file,
            "pending_count": pending_count,
            "timestamp": time.time(),
        }
        with open(WATCHER_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_watcher_status() -> Dict[str, Any]:
    """Reads live watcher status, checking PID vitality."""
    if not WATCHER_STATUS_FILE.exists():
        return {"running": False, "status": "STOPPED", "message": "Watcher daemon not started"}
    try:
        with open(WATCHER_STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        pid = data.get("pid")
        if not data.get("running") or not pid:
            return {"running": False, "status": "STOPPED", **data}
        try:
            os.kill(pid, 0)
            data["alive"] = True
            data["status"] = "RUNNING"
        except OSError:
            data["alive"] = False
            data["running"] = False
            data["status"] = "DEAD_STALE_PID"
        return data
    except Exception as exc:
        return {"running": False, "status": "ERROR", "error": str(exc)}


class CortexIncrementalSync:
    """Performs single-file incremental updates across Qdrant, Chunk Cache, and FalkorDB."""

    def __init__(
        self,
        collection: str = COLLECTION,
        symbols_graph: Optional[str] = None,
        embed_model_name: str = EMBED_MODEL_NAME,
        embed_base_url: str = EMBED_BASE_URL,
        qdrant_url: str = QDRANT_URL,
        viking: Optional[VikingChunker] = None,
    ):
        self.collection = collection
        self.symbols_graph = symbols_graph or (
            f"{collection}_symbols" if collection != "cortex_codebase" else os.getenv("FALKORDB_SYMBOLS_GRAPH", "cortex_symbols")
        )
        self.embed_model_name = embed_model_name
        self.embed_base_url = embed_base_url
        self.qdrant_url = qdrant_url

        self.cache = IngestCache()
        self.chunk_cache = ChunkEmbeddingCache()
        self.symbol_mgr = SymbolGraphManager(graph_name=self.symbols_graph)
        self.parser = SmartCodeNodeParser()
        self.viking = viking or VikingChunker()

        self.qdrant_client = AsyncQdrantClient(url=self.qdrant_url)
        self.sync_qdrant = QdrantClient(url=self.qdrant_url)

    async def sync_file(self, file_path: str) -> bool:
        """Incrementally syncs a created or modified file."""
        p = Path(file_path).resolve()
        resolved_fp = str(p)
        start_t = time.perf_counter()

        if not p.exists():
            return await self.delete_file(file_path)

        # 1. Check if content actually changed
        changed, mtime, sha, old_points = self.cache.is_file_changed(resolved_fp, self.collection)
        if not changed:
            return False

        # 2. Read file content
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            print(f"[Cortex Watcher] ⚠️  Cannot read {p.name}: {exc}", file=sys.stderr)
            return False

        # 3. Update FalkorDB Symbol Graph
        ext = p.suffix.lower()
        if ext in LANG_MAP:
            try:
                lang = LANG_MAP[ext]
                splitter = ASTCodeSplitter(language=lang)
                ast_info = splitter.extract_symbols_and_references(content, file_path=resolved_fp)
                self.symbol_mgr.save_file_ast(
                    file_path=resolved_fp,
                    declarations=ast_info.get("declarations", []),
                    imports=ast_info.get("imports", []),
                    references=ast_info.get("references", []),
                )
            except Exception as exc:
                print(f"[Cortex Watcher] ⚠️  Symbol extraction error on {p.name}: {exc}", file=sys.stderr)
        elif ext in (".md", ".markdown"):
            try:
                from cortex.symbols import MarkdownSymbolExtractor
                md_ext = MarkdownSymbolExtractor()
                md_info = md_ext.extract(content, file_path=resolved_fp)
                self.symbol_mgr.save_markdown_ast(
                    file_path=md_info["file_path"],
                    title=md_info["title"],
                    frontmatter=md_info["frontmatter"],
                    sections=md_info["sections"],
                    declarations=md_info["declarations"],
                    links=md_info["links"],
                    tags=md_info["tags"],
                )
            except Exception as exc:
                print(f"[Cortex Watcher] ⚠️  Markdown structure extraction error on {p.name}: {exc}", file=sys.stderr)

        # 4. Parse Chunks
        doc = Document(text=content, metadata={"file_path": resolved_fp, "file_name": p.name})
        nodes = self.parser.get_nodes_from_documents([doc])
        if not nodes:
            self.cache.record_file(resolved_fp, self.collection, mtime, sha, [])
            return True

        # 5. Delete old points from Qdrant before adding new ones
        if old_points:
            try:
                await self.qdrant_client.delete(
                    collection_name=self.collection,
                    points_selector=qmodels.PointIdsList(points=old_points),
                )
            except Exception:
                pass

        # 6. Lookup Cached Embeddings & Query Misses
        texts = [n.get_content() for n in nodes]
        cached_hits, misses = self.chunk_cache.get_batch(texts, self.embed_model_name)
        for idx, vec in cached_hits.items():
            nodes[idx].embedding = vec

        if misses:
            miss_texts = [item[1] for item in misses]
            try:
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    resp = await http_client.post(
                        f"{self.embed_base_url}/embeddings",
                        json={"model": self.embed_model_name, "input": miss_texts},
                    )
                    resp.raise_for_status()
                    data = resp.json()["data"]
                    new_cache_items = []
                    for (orig_idx, m_text), item in zip(misses, data):
                        emb = item["embedding"]
                        nodes[orig_idx].embedding = emb
                        new_cache_items.append((m_text, emb))
                    self.chunk_cache.put_batch(new_cache_items, self.embed_model_name)
            except Exception as exc:
                print(f"[Cortex Watcher] ⚠️  Embedding failed for {p.name}: {exc}", file=sys.stderr)

        # 7. Upsert to Qdrant
        points = []
        point_ids = []
        for n in nodes:
            if n.embedding:
                point_ids.append(n.node_id)
                points.append(
                    qmodels.PointStruct(
                        id=n.node_id,
                        vector={"text-dense": n.embedding},
                        payload={
                            "text": n.get_content(),
                            "file_path": resolved_fp,
                            "file_name": p.name,
                            **n.metadata,
                        },
                    )
                )

        if points:
            await self.qdrant_client.upsert(collection_name=self.collection, points=points)

        # 8. Record in IngestCache
        self.cache.record_file(resolved_fp, self.collection, mtime, sha, point_ids)

        # 9. Sync Viking hierarchical context
        try:
            self.viking.sync_file(p)
        except Exception:
            pass

        duration = time.perf_counter() - start_t
        hit_pct = round((len(cached_hits) / len(nodes)) * 100, 1) if nodes else 0.0
        print(
            f"[Cortex Watcher] ⚡ Synced '{p.name}' in {duration:.3f}s "
            f"({len(nodes)} chunks, {len(cached_hits)} cached [{hit_pct}%], {len(misses)} new vectors)"
        )
        return True

    async def delete_file(self, file_path: str) -> bool:
        """Purges a deleted file from Qdrant, Cache, FalkorDB, and Viking."""
        p = Path(file_path).resolve()
        resolved_fp = str(p)

        # Purge from Viking structural cache
        try:
            self.viking.delete_file(p)
        except Exception:
            pass

        old_points = self.cache.remove_file(resolved_fp, self.collection)
        if old_points:
            try:
                await self.qdrant_client.delete(
                    collection_name=self.collection,
                    points_selector=qmodels.PointIdsList(points=old_points),
                )
            except Exception:
                pass

        # Purge File node and edges in FalkorDB
        try:
            self.symbol_mgr._query(
                "MATCH (f:File {path: $path}) DETACH DELETE f",
                {"path": resolved_fp},
            )
        except Exception:
            pass

        print(f"[Cortex Watcher] 🗑️  Purged deleted file '{p.name}' from vector index, symbols, and Viking.")
        return True


class DebouncedChangeHandler(FileSystemEventHandler):
    """Gathers filesystem events and queues them with a debounce window."""

    def __init__(self, loop: asyncio.AbstractEventLoop, syncer: CortexIncrementalSync, debounce_sec: float = 2.0, root: str = "", collection: str = ""):
        self.loop = loop
        self.syncer = syncer
        self.debounce_sec = debounce_sec
        self.root = root
        self.collection = collection
        self.pending_events: Dict[str, float] = {}  # file_path -> last_event_time
        self.deleted_events: Set[str] = set()
        self.total_processed: int = 0
        self.last_event_time: Optional[float] = None
        self.last_synced_file: Optional[str] = None
        self._lock = asyncio.Lock()
        self._last_status_write = 0.0

    def _should_ignore(self, path_str: str) -> bool:
        p = Path(path_str)
        # Check extensions
        if p.is_file() and p.suffix.lower() not in SUPPORTED_EXT:
            return True
        # Check exclude patterns in path parts
        parts = set(p.parts)
        for ex in DEFAULT_EXCLUDE:
            clean_ex = ex.strip("*/")
            if clean_ex in parts:
                return True
        return False

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory and not self._should_ignore(event.src_path):
            self.last_event_time = time.time()
            self.pending_events[event.src_path] = self.last_event_time

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory and not self._should_ignore(event.src_path):
            self.last_event_time = time.time()
            self.pending_events[event.src_path] = self.last_event_time

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory and not self._should_ignore(event.src_path):
            self.last_event_time = time.time()
            self.deleted_events.add(event.src_path)
            self.pending_events.pop(event.src_path, None)

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            self.last_event_time = time.time()
            if not self._should_ignore(event.src_path):
                self.deleted_events.add(event.src_path)
                self.pending_events.pop(event.src_path, None)
            if not self._should_ignore(event.dest_path):
                self.pending_events[event.dest_path] = self.last_event_time

    async def flush_debounced(self):
        """Periodically flushes debounced items whose timer has expired."""
        now = time.time()
        to_process = []
        for path_str, last_t in list(self.pending_events.items()):
            if now - last_t >= self.debounce_sec:
                to_process.append(path_str)
                del self.pending_events[path_str]

        # Process deletions
        for path_str in list(self.deleted_events):
            self.deleted_events.remove(path_str)
            try:
                await self.syncer.delete_file(path_str)
                self.total_processed += 1
                self.last_synced_file = path_str
            except Exception as exc:
                print(f"[Cortex Watcher] Error purging {path_str}: {exc}", file=sys.stderr)

        # Process modifications / creations
        for path_str in to_process:
            try:
                synced = await self.syncer.sync_file(path_str)
                if synced:
                    self.total_processed += 1
                    self.last_synced_file = path_str
            except Exception as exc:
                print(f"[Cortex Watcher] Error syncing {path_str}: {exc}", file=sys.stderr)

        if now - self._last_status_write > 2.0 or to_process:
            self._last_status_write = now
            write_status(
                running=True,
                pid=os.getpid(),
                root=self.root,
                collection=self.collection,
                debounce_sec=self.debounce_sec,
                total_processed=self.total_processed,
                last_event_time=self.last_event_time,
                last_synced_file=self.last_synced_file,
                pending_count=len(self.pending_events) + len(self.deleted_events),
            )


async def start_watcher(
    watch_root: str,
    collection: str = COLLECTION,
    debounce_sec: float = DEFAULT_DEBOUNCE,
):
    """Starts the background directory observer and debounced flush loop."""
    root_path = Path(watch_root).resolve()
    print(f"[Cortex Watcher] 👁️  Starting file watcher on: {root_path}")
    print(f"[Cortex Watcher] Collection: {collection} | Debounce window: {debounce_sec}s")

    syncer = CortexIncrementalSync(collection=collection)
    loop = asyncio.get_running_loop()
    handler = DebouncedChangeHandler(
        loop=loop,
        syncer=syncer,
        debounce_sec=debounce_sec,
        root=str(root_path),
        collection=collection,
    )

    write_status(
        running=True,
        pid=os.getpid(),
        root=str(root_path),
        collection=collection,
        debounce_sec=debounce_sec,
        total_processed=0,
        pending_count=0,
    )

    observer = Observer()
    observer.schedule(handler, str(root_path), recursive=True)
    observer.start()

    stop_event = asyncio.Event()

    def _on_stop():
        print("\n[Cortex Watcher] Stopping watcher...")
        observer.stop()
        stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _on_stop)
        loop.add_signal_handler(signal.SIGTERM, _on_stop)
    except (NotImplementedError, RuntimeError):
        pass

    try:
        while not stop_event.is_set():
            await handler.flush_debounced()
            await asyncio.sleep(0.5)
    finally:
        observer.stop()
        observer.join()
        write_status(
            running=False,
            pid=os.getpid(),
            root=str(root_path),
            collection=collection,
            debounce_sec=debounce_sec,
            total_processed=handler.total_processed,
            last_event_time=handler.last_event_time,
            last_synced_file=handler.last_synced_file,
            pending_count=0,
        )
        print("[Cortex Watcher] Watcher stopped cleanly.")


def main():
    parser = argparse.ArgumentParser(description="Cortex Incremental Real-Time File Watcher")
    parser.add_argument("--root", default=".", help="Directory to monitor (default: current directory)")
    parser.add_argument("--collection", default=COLLECTION, help="Qdrant collection name")
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE, help="Debounce seconds (default: 2.0)")
    args = parser.parse_args()

    asyncio.run(start_watcher(watch_root=args.root, collection=args.collection, debounce_sec=args.debounce))


if __name__ == "__main__":
    main()
