"""
Batch ingestor: reads files from a directory tree and indexes them into Qdrant.
Optimized for high-throughput embedding on Apple Silicon Metal with concurrent batch evaluation
and Content-Hash Incremental Ingestion to skip unchanged files.

Usage:
  cortex-ingest --root /path/to/project [--collection my_collection] [--force]
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from llama_index.core import Settings, SimpleDirectoryReader, StorageContext, VectorStoreIndex
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client import models as qmodels

from cortex.cache import IngestCache, ChunkEmbeddingCache

load_dotenv()

EMBED_BASE_URL  = os.getenv("EMBED_BASE_URL", "http://localhost:8000/v1")
QDRANT_URL      = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION      = os.getenv("QDRANT_COLLECTION", "cortex_codebase")
DEFAULT_EXCLUDE = os.getenv(
    "INGEST_EXCLUDE_PATTERNS",
    ".git,node_modules,__pycache__,.venv,dist,build,*.gguf,*.pyc,*.log,*.lock,.DS_Store,coverage/,target/,*.min.js,vendor,vendors,third_party,extern,deps",
).split(",")

from cortex.chunking.languages import ALL_SUPPORTED_EXTS

SUPPORTED_EXT = ALL_SUPPORTED_EXTS


async def ingest(
    root: str,
    extra_excludes: Optional[List[str]] = None,
    collection: str = COLLECTION,
    force: bool = False,
    symbols_graph: Optional[str] = None,
):
    exclude = DEFAULT_EXCLUDE + (extra_excludes or [])
    target_symbols_db = symbols_graph or (
        f"{collection}_symbols" if collection != "cortex_codebase" else os.getenv("FALKORDB_SYMBOLS_GRAPH", "cortex_symbols")
    )
    print(f"[Cortex Ingest] Indexing: {root}")
    print(f"[Cortex Ingest] Qdrant Collection: {collection} | FalkorDB Symbol Graph: {target_symbols_db} (force={force})")

    embed_model_name = os.getenv("EMBED_MODEL_NAME", "mlx-community--Qwen3-Embedding-8B-4bit-DWQ")
    embed_api_key = os.getenv("EMBED_API_KEY") or os.getenv("OPENAI_API_KEY") or "not-needed"
    embed_model = OpenAIEmbedding(
        api_base=EMBED_BASE_URL,
        api_key=embed_api_key,
        model_name=embed_model_name,
        timeout=300.0,
        max_retries=3,
    )
    Settings.embed_model = embed_model

    client = AsyncQdrantClient(url=QDRANT_URL)
    sync_client = QdrantClient(url=QDRANT_URL)
    cache = IngestCache()

    # Ensure collection exists with hybrid dense + sparse support
    collections = await client.get_collections()
    existing = [c.name for c in collections.collections]

    needs_recreate = False
    if collection in existing:
        col_info = await client.get_collection(collection_name=collection)
        has_sparse = (
            col_info.config.params.sparse_vectors is not None
            and "text-sparse-new" in col_info.config.params.sparse_vectors
        )
        if not has_sparse:
            print(f"[Cortex Ingest] Upgrading collection '{collection}' to hybrid dense + sparse (BM25)...")
            await client.delete_collection(collection)
            cache.clear_collection(collection)
            needs_recreate = True

    embed_dim = int(os.getenv("EMBED_DIMENSION", "4096"))
    if collection not in existing or needs_recreate:
        await client.create_collection(
            collection_name=collection,
            vectors_config={"text-dense": qmodels.VectorParams(size=embed_dim, distance=qmodels.Distance.COSINE)},
            sparse_vectors_config={"text-sparse-new": qmodels.SparseVectorParams()},
        )
        print(f"[Cortex Ingest] Created hybrid collection '{collection}' (dense + sparse BM25)")
    else:
        print(f"[Cortex Ingest] Using existing hybrid collection '{collection}' (upsert mode)")

    store = QdrantVectorStore(
        client=sync_client,
        collection_name=collection,
        aclient=client,
        enable_hybrid=True,
        fastembed_sparse_model="Qdrant/bm25",
    )

    try:
        reader = SimpleDirectoryReader(
            input_dir=root,
            recursive=True,
            required_exts=list(SUPPORTED_EXT),
            exclude=exclude,
        )
        docs = reader.load_data()
    except Exception as e:
        print(f"[Cortex Ingest] Error reading directory: {e}", file=sys.stderr)
        return

    if not docs:
        print("[Cortex Ingest] ⚠️  No matching documents found.")
        return

    # 1. Content-Hash Incremental Ingestion Check
    current_files = {str(Path(d.metadata["file_path"]).resolve()) for d in docs if "file_path" in d.metadata}

    # Purge deleted files
    purged = cache.purge_missing_files(collection, current_files)
    if purged:
        print(f"[Cortex Ingest] 🗑️  Purging {len(purged)} deleted file(s) from collection '{collection}'...")
        for purged_file, old_points in purged:
            if old_points:
                try:
                    await client.delete(
                        collection_name=collection,
                        points_selector=qmodels.PointIdsList(points=old_points),
                    )
                except Exception as exc:
                    print(f"[Cortex Ingest] Warning: Failed to delete points for {purged_file}: {exc}", file=sys.stderr)

    docs_to_index = []
    files_to_reindex = {}  # {resolved_fp: (mtime, sha, old_points)}

    for doc in docs:
        fp = doc.metadata.get("file_path")
        if not fp:
            docs_to_index.append(doc)
            continue
        resolved_fp = str(Path(fp).resolve())
        if force:
            changed = True
            mtime, sha = IngestCache.get_file_stats(Path(resolved_fp))
            old_points = cache.get_all_cached_files(collection).get(resolved_fp, [])
        else:
            changed, mtime, sha, old_points = cache.is_file_changed(resolved_fp, collection)

        if changed:
            docs_to_index.append(doc)
            files_to_reindex[resolved_fp] = (mtime, sha, old_points)

    if not docs_to_index:
        print(f"[Cortex Ingest] ⚡ All {len(docs)} files are up-to-date in cache. Nothing to index.", flush=True)
        return

    print(f"[Cortex Ingest] Detected {len(docs_to_index)} modified or new file(s) out of {len(docs)} total files.")

    # Delete stale vector points for modified files before upserting new ones
    for resolved_fp, (_, _, old_points) in files_to_reindex.items():
        if old_points:
            try:
                await client.delete(
                    collection_name=collection,
                    points_selector=qmodels.PointIdsList(points=old_points),
                )
            except Exception:
                pass

    # 2. Extract AST symbols, imports, cross-file references, and markdown links into FalkorDB
    from cortex.symbols import SymbolGraphManager, MarkdownSymbolExtractor
    from cortex.chunking.ast_splitter import LANG_MAP, ASTCodeSplitter

    symbol_graph = SymbolGraphManager(graph_name=target_symbols_db)
    md_extractor = MarkdownSymbolExtractor(workspace_root=root)
    total_decls = 0
    total_refs = 0
    total_docs = 0
    total_doc_links = 0

    for doc in docs:
        fp = doc.metadata.get("file_path", "")
        ext = Path(fp).suffix.lower()
        if ext in LANG_MAP:
            try:
                splitter = ASTCodeSplitter(language=LANG_MAP[ext])
                res = splitter.extract_symbols_and_references(doc.text, file_path=fp)
                symbol_graph.save_file_ast(fp, res["declarations"], res["imports"], res["references"])
                total_decls += len(res["declarations"])
                total_refs += len(res["references"])
            except Exception as e:
                print(f"[Cortex Ingest] Warning extracting symbols for {fp}: {e}")
        elif ext in (".md", ".markdown"):
            try:
                md_res = md_extractor.extract(doc.text, file_path=fp)
                symbol_graph.save_markdown_ast(
                    file_path=md_res["file_path"],
                    title=md_res["title"],
                    frontmatter=md_res["frontmatter"],
                    sections=md_res["sections"],
                    declarations=md_res["declarations"],
                    links=md_res["links"],
                    tags=md_res["tags"],
                )
                total_docs += 1
                total_decls += len(md_res["declarations"])
                total_doc_links += len(md_res["links"])
            except Exception as e:
                print(f"[Cortex Ingest] Warning extracting markdown structure for {fp}: {e}")

    if total_decls > 0 or total_doc_links > 0 or total_docs > 0:
        msg_parts = []
        if total_decls > 0:
            msg_parts.append(f"{total_decls} symbols/sections")
        if total_refs > 0:
            msg_parts.append(f"{total_refs} code references")
        if total_docs > 0:
            msg_parts.append(f"{total_docs} markdown notes ({total_doc_links} links)")
        print(f"[Cortex Ingest] 🌐 Indexed {', '.join(msg_parts)} in FalkorDB ({target_symbols_db}).")

    # Auto-detect and ingest compiler-grade SCIP index if present in workspace root
    scip_candidates = [
        Path(root) / "index.scip",
        Path(root) / ".scip",
        Path(root) / "dump.scip",
    ]
    for scip_path in scip_candidates:
        if scip_path.is_file():
            try:
                from cortex.symbols.scip import SCIPImporter
                scip_imp = SCIPImporter(graph_name=target_symbols_db)
                scip_res = scip_imp.ingest_index(scip_path, purge_existing=False)
                print(
                    f"[Cortex Ingest] 🔬 Auto-ingested compiler-grade SCIP index ({scip_path.name}): "
                    f"{scip_res['symbols_indexed']} symbols, {scip_res['references_indexed']} references, "
                    f"{scip_res['relationships_indexed']} type relationships."
                )
                break
            except Exception as e:
                print(f"[Cortex Ingest] Warning auto-ingesting SCIP index {scip_path}: {e}")

    # 3. AST-aware hierarchical code & document chunking
    from cortex.chunking import SmartCodeNodeParser

    chunk_size = int(os.getenv("INGEST_CHUNK_SIZE", "2048"))
    chunk_overlap = int(os.getenv("INGEST_CHUNK_OVERLAP", "128"))
    parser = SmartCodeNodeParser(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    print(f"[Cortex Ingest] Chunking {len(docs_to_index)} files with Tree-Sitter AST & Markdown parser (size={chunk_size})...")
    nodes = parser.get_nodes_from_documents(docs_to_index, show_progress=True)

    ast_count = sum(1 for n in nodes if n.metadata.get("chunk_type") == "ast_code")
    skel_count = sum(1 for n in nodes if n.metadata.get("chunk_type") == "ast_skeleton")
    md_count = sum(1 for n in nodes if n.metadata.get("chunk_type") == "markdown_section")
    cfg_count = sum(1 for n in nodes if n.metadata.get("chunk_type") == "config_section")
    txt_count = sum(1 for n in nodes if n.metadata.get("chunk_type") == "text_sentence")
    print(f"[Cortex Ingest] Generated {len(nodes)} chunks (AST code: {ast_count}, Skeletons: {skel_count}, Markdown: {md_count}, Config: {cfg_count}, Text: {txt_count}).")

    if not nodes:
        # Files might be empty or contained only excluded content
        for resolved_fp, (mtime, sha, _) in files_to_reindex.items():
            cache.record_file(resolved_fp, collection, mtime, sha, [])
        print(f"[Cortex Ingest] ✅ Cache updated for empty/unindexable files.")
        return

    # 4. Concurrent batch embedding with Chunk-Level SHA-256 Cache
    chunk_cache = ChunkEmbeddingCache()
    all_texts = [n.get_content() for n in nodes]
    cached_hits, cache_misses = chunk_cache.get_batch(all_texts, embed_model_name)

    # Assign cached embeddings immediately
    for orig_idx, vec in cached_hits.items():
        nodes[orig_idx].embedding = vec

    hit_pct = (len(cached_hits) / len(nodes) * 100) if nodes else 0.0
    print(f"[Cortex Ingest] ⚡ Chunk Cache: {len(cached_hits)}/{len(nodes)} chunks cached ({hit_pct:.1f}% hit rate). Embedding {len(cache_misses)} new chunks.")

    batch_size = int(os.getenv("EMBED_BATCH_SIZE", "8"))
    concurrency = int(os.getenv("EMBED_CONCURRENCY", "1"))

    if cache_misses:
        miss_batches = [cache_misses[i:i + batch_size] for i in range(0, len(cache_misses), batch_size)]
        total_batches = len(miss_batches)
        print(f"[Cortex Ingest] Querying oMLX for {len(cache_misses)} chunks in {total_batches} batches (concurrency={concurrency}, batch_size={batch_size})...", flush=True)

        sem = asyncio.Semaphore(concurrency)
        completed_batches = 0

        limits = httpx.Limits(max_keepalive_connections=32, max_connections=64)
        timeout = httpx.Timeout(600.0, connect=30.0, read=600.0)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as http_client:
            async def embed_batch(batch_idx: int, miss_batch):
                nonlocal completed_batches
                async with sem:
                    texts = [item[1] for item in miss_batch]
                    for attempt in range(3):
                        try:
                            resp = await http_client.post(
                                f"{EMBED_BASE_URL}/embeddings",
                                json={"model": embed_model_name, "input": texts},
                            )
                            resp.raise_for_status()
                            data = resp.json()["data"]
                            new_cache_items = []
                            for (orig_idx, miss_text), item in zip(miss_batch, data):
                                emb = item["embedding"]
                                nodes[orig_idx].embedding = emb
                                new_cache_items.append((miss_text, emb))
                            chunk_cache.put_batch(new_cache_items, embed_model_name)
                            break
                        except Exception as exc:
                            err_name = f"{type(exc).__name__}: {exc or 'timeout'}"
                            if attempt == 2:
                                print(f"[Cortex Ingest] ⚠️  Batch {batch_idx+1}/{total_batches} failed after 3 retries ({err_name}). Skipping batch.", file=sys.stderr, flush=True)
                            else:
                                await asyncio.sleep(2.0 * (attempt + 1))

                    completed_batches += 1
                    if completed_batches % 5 == 0 or completed_batches == total_batches:
                        chunks_done = min(completed_batches * batch_size, len(cache_misses))
                        pct = (completed_batches / total_batches) * 100
                        print(f"[Cortex Ingest] Progress: {completed_batches}/{total_batches} batches ({pct:.1f}%), {chunks_done}/{len(cache_misses)} new chunks", flush=True)

            await asyncio.gather(*[embed_batch(i, b) for i, b in enumerate(miss_batches)])

    # 4. Store into Qdrant in batch (only nodes with valid embeddings)
    valid_nodes = [n for n in nodes if n.embedding is not None]
    print(f"[Cortex Ingest] Writing {len(valid_nodes)} vectors to Qdrant...", flush=True)
    storage_context = StorageContext.from_defaults(vector_store=store)
    VectorStoreIndex(
        nodes=valid_nodes,
        storage_context=storage_context,
        embed_model=embed_model,
        show_progress=True,
    )

    # 5. Update Cache with new point IDs
    file_to_nodes = {}
    for n in valid_nodes:
        fp = n.metadata.get("file_path")
        if fp:
            file_to_nodes.setdefault(str(Path(fp).resolve()), []).append(n.node_id)

    for resolved_fp, (mtime, sha, _) in files_to_reindex.items():
        node_ids = file_to_nodes.get(resolved_fp, [])
        cache.record_file(resolved_fp, collection, mtime, sha, node_ids)

    print(f"[Cortex Ingest] Done: {len(valid_nodes)} chunks from {len(docs_to_index)} modified files indexed into '{collection}'", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Cortex workspace ingestor")
    parser.add_argument("--root", required=True, help="Directory to index")
    parser.add_argument("--collection", default=COLLECTION, help="Qdrant collection name")
    parser.add_argument("--symbols-graph", default=None, help="FalkorDB symbol graph name (defaults to <collection>_symbols)")
    parser.add_argument("--exclude", nargs="*", default=[], help="Extra exclusion patterns")
    parser.add_argument("--force", action="store_true", help="Force re-index all files ignoring cache")
    args = parser.parse_args()

    if not Path(args.root).exists():
        print(f"[Cortex Ingest] Error: {args.root} does not exist", file=sys.stderr)
        sys.exit(1)

    asyncio.run(ingest(args.root, extra_excludes=args.exclude, collection=args.collection, force=args.force, symbols_graph=args.symbols_graph))


if __name__ == "__main__":
    main()
