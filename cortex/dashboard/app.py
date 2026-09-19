"""
Cortex web dashboard: local observability and interactive graph explorer.
Port :8004 (configurable via DASHBOARD_PORT).
"""
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

from cortex.dashboard.graph_views import build_graph_view, build_symbol_impact_view
from cortex.falkordb_client import get_shared_falkordb_client

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
CORTEX_ROOT = BASE_DIR.parent.parent
JOURNAL_DIR = Path(os.getenv("JOURNAL_DIR", str(CORTEX_ROOT / "journal")))
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8004"))
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_DB = os.getenv("FALKORDB_DATABASE", "cortex")

app = FastAPI(title="Cortex Dashboard", version="0.2.0")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _check_tcp(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _check_http(url: str, timeout: float = 2.0) -> tuple[bool, Optional[dict]]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code < 400:
                try:
                    return True, resp.json()
                except Exception:
                    return True, None
            return False, None
    except Exception:
        return False, None


@app.get("/api/status")
async def get_status():
    qdrant_ok, _ = await _check_http("http://localhost:6333/collections")
    falkordb_ok = _check_tcp(FALKORDB_HOST, FALKORDB_PORT)
    falkor_browser_ok, _ = await _check_http("http://localhost:3000")
    omlx_ok, omlx_data = await _check_http("http://localhost:8000/health")

    return {
        "services": [
            {
                "name": "Qdrant Vector Store",
                "port": 6333,
                "url": "http://localhost:6333/dashboard",
                "status": "RUNNING" if qdrant_ok else "DOWN",
                "healthy": qdrant_ok,
                "description": "Dense code chunk retrieval (4096-dim vectors)",
            },
            {
                "name": "FalkorDB Graph Engine",
                "port": 6379,
                "url": "http://localhost:3000",
                "status": "RUNNING" if falkordb_ok else "DOWN",
                "healthy": falkordb_ok,
                "description": "GraphBLAS native temporal property graph (Web UI on :3000)",
            },
            {
                "name": "FalkorDB Browser UI",
                "port": 3000,
                "url": "http://localhost:3000",
                "status": "RUNNING" if falkor_browser_ok else "DOWN",
                "healthy": falkor_browser_ok,
                "description": "Official Cypher query visualizer & browser",
            },
            {
                "name": "oMLX Admin Dashboard",
                "port": 8000,
                "url": "http://localhost:8000/admin/dashboard",
                "status": "RUNNING" if omlx_ok else "DOWN",
                "healthy": omlx_ok,
                "description": "oMLX web console & model management dashboard",
                "meta": omlx_data,
            },
        ],
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/collections")
async def list_collections():
    """List Qdrant collections with point counts and configs."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:6333/collections")
            if resp.status_code != 200:
                return {"status": "error", "collections": []}
            data = resp.json().get("result", {}).get("collections", [])
            details = []
            for c in data:
                name = c["name"]
                info_resp = await client.get(f"http://localhost:6333/collections/{name}")
                info = info_resp.json().get("result", {}) if info_resp.status_code == 200 else {}
                details.append({
                    "name": name,
                    "status": info.get("status", "unknown"),
                    "points_count": info.get("points_count", 0),
                    "vectors_count": info.get("vectors_count", 0),
                    "indexed_vectors_count": info.get("indexed_vectors_count", 0),
                })
            return {"status": "ok", "collections": details}
    except Exception as e:
        return {"status": "error", "message": str(e), "collections": []}


@app.post("/api/collections")
async def create_collection(request: Request):
    """Creates a new Qdrant collection configured for Qwen3-Embedding (4096-dim, Cosine)."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return {"status": "error", "message": "Collection name is required"}
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            payload = {
                "vectors": {
                    "size": 4096,
                    "distance": "Cosine"
                }
            }
            resp = await client.put(f"http://localhost:6333/collections/{name}", json=payload)
            if resp.status_code in [200, 201]:
                return {"status": "ok", "message": f"Collection '{name}' created successfully."}
            else:
                return {"status": "error", "message": resp.text}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/graphs")
async def list_graphs():
    """Lists all active graph databases in FalkorDB."""
    try:
        client = get_shared_falkordb_client(host=FALKORDB_HOST, port=FALKORDB_PORT)
        graphs = client.get_redis().execute_command("GRAPH.LIST")
        return {"status": "ok", "graphs": graphs if isinstance(graphs, list) else []}
    except Exception as e:
        return {"status": "error", "message": str(e), "graphs": []}


@app.get("/api/graph")
async def get_graph(graph_type: str = "episodic", clean: bool = True, graph_name: Optional[str] = None):
    """Queries FalkorDB for nodes and relationships formatted for vis-network."""
    return build_graph_view(
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
        default_db=FALKORDB_DB,
        graph_type=graph_type,
        clean=clean,
        graph_name=graph_name,
    )


@app.get("/api/symbols")
async def get_symbols(q: Optional[str] = None):
    """Returns symbol graph statistics or symbol references if query 'q' provided."""
    from cortex.symbols.graph import SymbolGraphManager
    try:
        sym_mgr = SymbolGraphManager()
        if q:
            res = sym_mgr.find_references(q)
            return {"status": "ok", "result": res}
        stats = sym_mgr.get_statistics()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/symbols/impact")
async def get_symbol_impact(
    target: Optional[str] = None,
    file_path: Optional[str] = None,
    symbol: Optional[str] = None,
    direction: str = "upstream",
    max_depth: int = 3,
):
    """
    Performs blast-radius and impact analysis for a symbol or file.
    Returns structured nodes, edges, risk scores, and tree hierarchies for interactive visualization.
    """
    return build_symbol_impact_view(
        target=target,
        file_path=file_path,
        symbol=symbol,
        direction=direction,
        max_depth=max_depth,
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
    )


@app.get("/api/symbols/autocomplete")
async def autocomplete_symbols(q: str = "", limit: int = 30):
    """Fast autocomplete search for symbols and files."""
    try:
        client = get_shared_falkordb_client(host=FALKORDB_HOST, port=FALKORDB_PORT)
        q_clean = q.strip().replace("'", "").replace('"', "")
        if q_clean:
            cypher = (
                f"MATCH (s:Symbol) WHERE toLower(s.name) CONTAINS toLower('{q_clean}') "
                f"RETURN DISTINCT s.name, 'symbol' ORDER BY size(s.name) LIMIT {limit}"
            )
        else:
            cypher = f"MATCH (s:Symbol) RETURN DISTINCT s.name, 'symbol' ORDER BY s.name LIMIT {limit}"
        
        res = client.query("cortex_symbols", cypher)
        items = []
        if res and len(res) > 1 and isinstance(res[1], list):
            for row in res[1]:
                items.append({"name": str(row[0]), "type": str(row[1])})

        if len(items) < limit:
            rem = limit - len(items)
            f_cypher = (
                f"MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower('{q_clean}') RETURN DISTINCT f.path, 'file' LIMIT {rem}"
                if q_clean else f"MATCH (f:File) RETURN DISTINCT f.path, 'file' LIMIT {rem}"
            )
            f_res = client.query("cortex_symbols", f_cypher)
            if f_res and len(f_res) > 1 and isinstance(f_res[1], list):
                for row in f_res[1]:
                    items.append({"name": str(row[0]), "type": "file"})

        return {"status": "ok", "items": items}
    except Exception as e:
        return {"status": "error", "message": str(e), "items": []}


@app.get("/api/cache/stats")
async def get_cache_stats():
    """Returns chunk embedding cache and file ingestion cache metrics."""
    from cortex.cache import ChunkEmbeddingCache, IngestCache
    try:
        chunk_cache = ChunkEmbeddingCache()
        chunk_stats = chunk_cache.get_stats()
        ingest_cache = IngestCache()
        all_files = ingest_cache.get_all_cached_files("cortex_codebase")
        return {
            "status": "ok",
            "chunk_cache": chunk_stats,
            "total_indexed_files": len(all_files),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/cache/clear")
async def clear_cache():
    """Clears the chunk embedding SQLite cache."""
    from cortex.cache import ChunkEmbeddingCache
    try:
        chunk_cache = ChunkEmbeddingCache()
        purged_count = chunk_cache.clear()
        return {"status": "ok", "purged_chunks": purged_count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/watcher/status")
async def get_watcher_endpoint():
    """Returns real-time watcher daemon health and queue statistics."""
    from cortex.watcher import get_watcher_status
    try:
        st = get_watcher_status()
        return {"status": "ok", "watcher": st}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/scip/auto")
async def run_scip_auto():
    """Triggers automated SCIP indexing and ingestion."""
    import shutil, subprocess
    scip_path = CORTEX_ROOT / "index.scip"
    scip_py = shutil.which("scip-python")
    if scip_path.exists():
        from cortex.symbols.scip import SCIPImporter
        importer = SCIPImporter()
        stats = importer.ingest_scip_file(str(scip_path))
        return {"status": "ok", "message": "Ingested existing index.scip", "stats": stats}
    elif scip_py:
        try:
            cmd = [scip_py, "index", "--project-root", str(CORTEX_ROOT), "--output", str(scip_path)]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if p.returncode == 0 and scip_path.exists():
                from cortex.symbols.scip import SCIPImporter
                importer = SCIPImporter()
                stats = importer.ingest_scip_file(str(scip_path))
                return {"status": "ok", "message": "Generated and ingested index.scip", "stats": stats}
            return {"status": "error", "message": f"scip-python failed: {p.stderr}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "not_installed",
            "message": "Neither index.scip nor scip-python CLI tool was found. Run `npm install -g @sourcegraph/scip-python` or generate index.scip."
        }


@app.get("/api/journal")
async def get_journal():
    """Parses all journal files and returns chronological entries."""
    if not JOURNAL_DIR.is_dir():
        return {"entries": []}

    entries = []
    files = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)
    for f in files:
        date_str = f.stem
        try:
            content = f.read_text(encoding="utf-8")
            # Split sections by ##
            sections = re.split(r"(?m)^##\s+", content)
            for sec in sections[1:]:
                lines = sec.strip().split("\n")
                if not lines:
                    continue
                header = lines[0].strip()
                body = "\n".join(lines[1:]).strip()

                # Extract tags and project if present
                tags = []
                project = None
                tag_match = re.search(r"\*\*Tags:\*\*\s*(.*)", body)
                if tag_match:
                    tags = [t.strip() for t in tag_match.group(1).split(",")]
                proj_match = re.search(r"\[Project:\s*([^\]]+)\]", body) or re.search(r"\*\*Project:\*\*\s*(.*)", body)
                if proj_match:
                    project = proj_match.group(1).strip()

                entries.append({
                    "date": date_str,
                    "title": header,
                    "body": body,
                    "tags": tags,
                    "project": project,
                })
        except Exception:
            continue

    return {"entries": entries}


@app.post("/api/dream")
async def trigger_dream(request: Request):
    """Triggers on-demand Cortex Dream Mode consolidation cycle."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    project = body.get("project", "cortex")
    purge_stale = bool(body.get("purge_stale", False))

    from cortex.dream import CortexDreamEngine
    engine = CortexDreamEngine()
    try:
        report = await engine.dream(project=project, purge_stale=purge_stale)
        return {"status": "ok", "report": report}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/query")
async def run_query(request: Request):
    """Unified 4-Tier retrieval test endpoint."""
    body = await request.json()
    query_text = body.get("query", "").strip()
    collection = body.get("collection")
    if not query_text:
        return JSONResponse(status_code=400, content={"error": "query cannot be empty"})

    from cortex.retrieval.engine import RetrievalEngine
    from cortex.episodic.driver import AgentNativeEpisodicDriver
    from cortex.viking import VikingChunker
    from cortex.symbols.graph import SymbolGraphManager
    from cortex.graph import _extract_symbol_candidates

    # Tier 0: Deterministic Symbols & Cross-File References
    symbols = []
    try:
        sym_mgr = SymbolGraphManager()
        cands = _extract_symbol_candidates(query_text)
        for c in cands:
            res = sym_mgr.find_references(c)
            if res and (res.get("definitions") or res.get("references") or res.get("imports")):
                symbols.append(res)
    except Exception as e:
        symbols = [{"error": str(e)}]

    # Tier 1: Episodic Facts (Agent-Native FalkorDB driver)
    facts = []
    try:
        mem = AgentNativeEpisodicDriver()
        facts = await mem.search(query_text, limit=5)
    except Exception as e:
        facts = [f"Episodic error: {e}"]

    # Tier 2: Viking Structure
    viking_summary = ""
    try:
        vk = VikingChunker(workspace_root=os.getenv("WORKSPACE_ROOTS") or str(Path.home()))
        viking_summary = vk.get_context_for_query(query_text)
    except Exception as e:
        viking_summary = f"Viking error: {e}"

    # Tier 3: Dense Retrieval + Rerank
    snippets = []
    try:
        engine = RetrievalEngine()
        await engine.initialize()
        snippets = await engine.query(query_text, collection=collection)
    except Exception as e:
        snippets = [f"Retrieval error: {e}"]

    return {
        "query": query_text,
        "tier0_symbols": symbols,
        "tier1_facts": facts,
        "tier2_viking": viking_summary,
        "tier3_snippets": snippets,
    }


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


if __name__ == "__main__":
    uvicorn.run("cortex.dashboard.app:app", host="0.0.0.0", port=DASHBOARD_PORT, reload=False)
