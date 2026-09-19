"""
FastMCP server: main integration point for AI agents.
Exposes seven tools to Antigravity CLI agents via stdio transport:

  1. search_knowledge_and_memory: full LangGraph RAG pipeline (read)
  2. record_system_outcome: persist directly to FalkorDB KG and journal (write)
  3. trigger_dream_mode: on-demand synaptic pruning and community detection (write)
  4. index_directory: on-demand Qdrant ingest (write)
  5. check_cortex_health: service health dashboard (read)
  6. delete_or_supersede_fact: KG fact management (write)
  7. trace_symbol_impact: blast radius and impact analysis (read)
"""
import asyncio
import json
import os
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastmcp import FastMCP

from cortex.graph import CortexState, make_graph
from cortex.retrieval import RetrievalEngine
from cortex.viking import VikingChunker
from cortex.episodic import AgentNativeEpisodicDriver
from cortex.ingest import ingest as _ingest
from cortex.symbols.graph import SymbolGraphManager

# Deterministically load .env from CORTEX_ROOT regardless of caller's CWD
CORTEX_ROOT = Path(__file__).resolve().parent.parent
CORTEX_ENV_PATH = CORTEX_ROOT / ".env"
if CORTEX_ENV_PATH.is_file():
    load_dotenv(CORTEX_ENV_PATH)
load_dotenv()

# ── Singletons (shared across tool calls) ──────────────────────────────────

retrieval    = RetrievalEngine()
viking       = VikingChunker(workspace_root=os.getenv("WORKSPACE_ROOTS") or str(Path.home()))
episodic     = AgentNativeEpisodicDriver()
symbol_graph = SymbolGraphManager()
_graph       = None  # built after async init

JOURNAL_DIR = Path(os.getenv("JOURNAL_DIR", str(CORTEX_ROOT / "journal")))
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app):
    global _graph
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: os._exit(0))
        loop.add_signal_handler(signal.SIGINT, lambda: os._exit(0))
    except (NotImplementedError, RuntimeError):
        pass
    await retrieval.initialize()
    await episodic.initialize()
    _graph = make_graph(retrieval, viking, episodic, symbol_graph=symbol_graph)
    yield
    await episodic.close()


mcp = FastMCP(
    name="cortex",
    instructions=(
        "Cortex is your grounded memory, code intelligence, and retrieval base with four knowledge tiers: "
        "(0) AST Symbols & Backlinks, (1) Operational Facts & Decisions, (2) Viking Code Structure, (3) Qdrant Vectors. "
        "ALWAYS call search_knowledge_and_memory before writing code or making architectural decisions. "
        "ALWAYS call trace_symbol_impact before refactoring functions, classes, or interfaces. "
        "ALWAYS call record_system_outcome after completing significant work or configuration changes."
    ),
    lifespan=lifespan,
)


# ── Tool 1: Retrieve ───────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Query the Cortex RAG and unified memory pipeline. Returns a structured Markdown "
        "block with grounded context tiers: (0) Deterministic Symbol Declarations & Cross-File "
        "References from the FalkorDB symbol graph (exact definitions, imports, callers), "
        "(1) Operational Facts & Decisions from the temporal knowledge graph (Graphiti/FalkorDB), "
        "(2) System Architecture Overview from the Viking hierarchical code map, and "
        "(3) Ground-Truth Code References from Qdrant with Qwen3 cross-encoder reranking. "
        "CALL THIS before writing code, making architectural decisions, or answering "
        "questions about the local codebase structure."
    )
)
async def search_knowledge_and_memory(
    prompt: str,
    collection: Optional[str] = None,
    workspace_root: Optional[str] = None,
) -> str:
    """
    Args:
        prompt:         The coding task, architectural question, or query to retrieve context for.
        collection:     Optional Qdrant collection name to search within (defaults to 'cortex_codebase', or 'all' for federation).
        workspace_root: Optional workspace path to scope OpenViking hierarchical structure to.
    Returns:
        Structured Markdown with exact symbol references, operational decisions, and code snippets.
    """
    state = CortexState(
        query=prompt,
        collection=collection,
        workspace_root=workspace_root,
        kg_facts=[],
        viking_summaries="",
        doc_snippets=[],
        symbol_results=[],
        final_context="",
    )
    result = await _graph.ainvoke(state)
    return result["final_context"]


# ── Tool 2: Record ─────────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Record a significant technical decision, configuration change, or work outcome. "
        "Persists to: (1) FalkorDB episodic knowledge graph directly without intermediate LLM proxy, "
        "and (2) a dated Markdown journal file for human-readable audit trail. Old conflicting facts "
        "are superseded, not deleted. Can accept structured entities/facts directly or raw narrative. "
        "CALL THIS after: changing env vars / ports / dependencies, choosing an architecture, "
        "fixing a non-trivial bug, or completing any substantial task."
    )
)
async def record_system_outcome(
    episode_name: str,
    content: str,
    tags: Optional[list[str]] = None,
    project: Optional[str] = None,
    entities: Optional[list[dict[str, str]]] = None,
    facts: Optional[list[dict[str, str]]] = None,
    supersedes: Optional[list[str]] = None,
) -> str:
    """
    Args:
        episode_name: Short title (5-10 words), e.g. 'Switched embed server to port 8001'
        content:      Full description of the outcome, decision, or change.
        tags:         Optional list of lowercase keyword tags, e.g. ['embedding', 'ports']
        project:      Optional project label, e.g. 'cortex' or 'big-je'
        entities:     Optional structured entities [{'name': '...', 'type': '...', 'summary': '...'}]
        facts:        Optional structured facts [{'source': '...', 'relation': '...', 'target': '...', 'fact': '...'}]
        supersedes:   Optional list of patterns/facts superseded by this outcome
    Returns:
        Confirmation with paths to KG and journal.
    """
    full_content = content
    if project:
        full_content = f"[Project: {project}]\n\n{content}"

    # Append to daily Markdown journal first (so data is never lost)
    today        = datetime.now().strftime("%Y-%m-%d")
    now_time     = datetime.now().strftime("%H:%M")
    journal_file = JOURNAL_DIR / f"{today}.md"
    entry_lines  = [f"\n## {now_time} - {episode_name}\n", full_content]
    if tags:
        entry_lines.append(f"\n**Tags:** {', '.join(tags)}")
    if project:
        entry_lines.append(f"\n**Project:** {project}")
    if supersedes:
        entry_lines.append(f"\n**Supersedes:** {', '.join(supersedes)}")
    entry_lines.append("\n")

    with open(journal_file, "a") as f:
        f.write("\n".join(entry_lines))

    # Persist to FalkorDB KG directly (fast, agent-native, no middle proxy daemon)
    kg_status = "✅ FalkorDB Episodic Memory"
    try:
        res = await episodic.record_outcome(
            episode_name=episode_name,
            content=full_content,
            tags=tags,
            project=project,
            entities=entities,
            facts=facts,
            supersedes=supersedes,
        )
        kg_status = f"✅ FalkorDB KG ({res.get('entities_written', 0)} entities, {res.get('facts_written', 0)} facts, {res.get('superseded_count', 0)} superseded)"
    except Exception as e:
        kg_status = f"⚠️ FalkorDB KG skipped ({type(e).__name__}: {e})"

    return (
        f"✅ Recorded: '{episode_name}'\n"
        f"   → Journal: {journal_file}\n"
        f"   → {kg_status}"
    )


# ── Tool 3: Dream Mode ─────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Trigger an on-demand Cortex Dream Mode consolidation cycle. Executes: "
        "(1) Synaptic pruning & stale fact contradiction resolution, archiving invalid facts to disk, "
        "(2) Graph community detection (Louvain algorithm) clustering code symbols into subsystems, "
        "(3) Abstract invariant induction across codebase decisions, and "
        "(4) Generates a dated dream report in cortex/journal/dreams/."
    )
)
async def trigger_dream_mode(
    project: str = "cortex",
    purge_stale: bool = False,
    invariants: Optional[list[dict[str, str]]] = None,
    stale_fact_names: Optional[list[str]] = None,
) -> str:
    """
    Args:
        project:          Target project label (default: 'cortex').
        purge_stale:      If True, physically removes superseded facts after archiving. Default False (soft-prune).
        invariants:       Optional list of newly induced architectural invariants: [{'domain': '...', 'rule': '...', 'rationale': '...'}].
        stale_fact_names: Optional list of older fact names to supersede during pruning.
    Returns:
        Structured summary of the dream consolidation report.
    """
    from cortex.dream import CortexDreamEngine
    engine = CortexDreamEngine()
    report = await engine.dream(
        project=project,
        purge_stale=purge_stale,
        invariants=invariants,
        stale_fact_names=stale_fact_names,
    )
    return (
        f"🌙 Dream Mode completed for project '{project}':\n"
        f"- Active Facts Remaining: {report['pruned_facts']['active_facts_remaining']}\n"
        f"- Superseded Facts Pruned: {report['pruned_facts']['superseded_facts_pruned']}\n"
        f"- Contradictions Flagged: {len(report['pruned_facts']['contradictions_flagged'])}\n"
        f"- Subsystem Clusters Found: {report['subsystem_clusters']}\n"
        f"- Invariants Active: {report['invariants_inducted']}\n"
        f"- Dream Report Written: {report['dream_report_path']}\n"
        f"- Invariants Log: {report['invariants_path']}"
    )


# ── Tool 4: Index ──────────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Index a directory into the Qdrant vector store so its files become "
        "retrievable by search_knowledge_and_memory. Uses upsert semantics: "
        "existing vectors are updated, not destroyed. Safe to run multiple times. "
        "Use this when adding a new project or re-indexing after major refactors."
    )
)
async def index_directory(
    path: str,
    collection: Optional[str] = None,
    symbols_graph: Optional[str] = None,
    exclude_patterns: Optional[list[str]] = None,
) -> str:
    """
    Args:
        path:             Absolute path to the directory to index.
        collection:       Optional Qdrant collection name (defaults to 'cortex_codebase').
        symbols_graph:    Optional FalkorDB symbol graph name (defaults to '<collection>_symbols').
        exclude_patterns: Additional glob patterns to exclude (e.g. ['tests/', '*.spec.ts']).
    Returns:
        Confirmation with document count.
    """
    if not Path(path).exists():
        return f"❌ Path does not exist: {path}"
    target_col = collection or os.getenv("QDRANT_COLLECTION", "cortex_codebase")
    target_sym = symbols_graph or (f"{target_col}_symbols" if target_col != "cortex_codebase" else "cortex_symbols")
    asyncio.create_task(_ingest(path, extra_excludes=exclude_patterns or [], collection=target_col, symbols_graph=target_sym))
    return f"🚀 Indexing started in background for '{path}' into Qdrant collection '{target_col}' and FalkorDB graph '{target_sym}'."


# ── Tool 5: Health ─────────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Check the health of all Cortex backend services. Returns a JSON status "
        "report for Qdrant, FalkorDB, the inference server (embeddings/reranker), and the dashboard. "
        "Call this if other Cortex tools return errors or time out."
    )
)
async def check_cortex_health() -> str:
    """Returns JSON with status of each service."""
    import httpx
    import socket

    status = {}

    # Dynamic service URLs
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    embed_url = os.getenv("EMBED_BASE_URL", "http://localhost:8000/v1")
    base_host = embed_url.replace("/v1", "").rstrip("/")
    inference_health = os.getenv(
        "INFERENCE_HEALTH_URL",
        f"{base_host}/health" if ("localhost" in base_host or "127.0.0.1" in base_host) else embed_url
    )
    dash_host = os.getenv("DASHBOARD_HOST", "localhost")
    dash_port = os.getenv("DASHBOARD_PORT", "8004")

    # HTTP-based checks
    http_checks = {
        "qdrant": f"{qdrant_url}/healthz",
        "inference_server": inference_health,
        "cortex_dashboard": f"http://{dash_host}:{dash_port}/api/cache/stats",
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in http_checks.items():
            try:
                resp = await client.get(url)
                status[name] = "✅ RUNNING" if resp.status_code < 500 else f"⚠️  HTTP {resp.status_code}"
            except Exception as e:
                status[name] = f"❌ DOWN ({type(e).__name__})"

    # FalkorDB TCP check (Redis protocol)
    falkor_host = os.getenv("FALKORDB_HOST", "localhost")
    falkor_port = int(os.getenv("FALKORDB_PORT", "6379"))
    try:
        sock = socket.create_connection((falkor_host, falkor_port), timeout=2)
        sock.close()
        status["falkordb"] = "✅ RUNNING"
    except Exception as e:
        status["falkordb"] = f"❌ DOWN ({e})"

    # Overall recommendation
    if any("❌ DOWN" in str(v) for v in status.values()):
        status["recommendation"] = f"⚠️  One or more Cortex services are down. Run 'cd {CORTEX_ROOT} && make start' to bring them online."
    else:
        status["recommendation"] = "✅ All core Cortex services are operational."

    return json.dumps(status, indent=2)


# ── Tool 6: Delete / Supersede ─────────────────────────────────────────────

@mcp.tool(
    description=(
        "Mark a specific Graphiti fact as superseded (soft-delete). Use when a previously "
        "recorded fact is no longer valid, such as when a port changed, a dependency was removed, "
        "or a past decision was reversed. The fact is marked invalid with your reason, "
        "preserving audit history. Requires the fact episode name or ID from a prior search."
    )
)
async def delete_or_supersede_fact(fact_id: str, reason: str) -> str:
    """
    Args:
        fact_id: The episode name or ID of the fact to supersede.
        reason:  Why this fact is no longer valid.
    Returns:
        Confirmation message.
    """
    await episodic.delete_fact(fact_id, reason)
    return f"✅ Fact '{fact_id}' marked as superseded.\nReason recorded: {reason}"


# ── Tool 7: Blast-Radius / Impact Analysis ─────────────────────────────────

@mcp.tool(
    description=(
        "Trace the upstream callers or downstream dependencies of a symbol across the codebase. "
        "Performs fast graph path traversal in FalkorDB over REFERENCES, IMPORTS, CALLS, and "
        "IMPLEMENTS edges. CALL THIS before refactoring or deleting a function, class, or interface "
        "to discover all call sites and dependent files that could break."
    )
)
async def trace_symbol_impact(
    symbol: str,
    direction: str = "upstream",
    max_depth: int = 3,
    symbols_graph: Optional[str] = None,
) -> str:
    """
    Args:
        symbol:        Name of the function, class, method, or struct to analyze.
        direction:     'upstream' (dependents/callers who will break) or 'downstream' (callees/dependencies).
        max_depth:     Graph search depth hops (1 to 5, default: 3).
        symbols_graph: Optional FalkorDB graph name (defaults to 'cortex_symbols').
    Returns:
        Structured JSON report showing all impacted files, lines, and relationships.
    """
    target_graph = symbols_graph or os.getenv("FALKORDB_SYMBOLS_GRAPH", "cortex_symbols")
    mgr = symbol_graph if target_graph == symbol_graph.graph_name else SymbolGraphManager(graph_name=target_graph)
    impact = mgr.trace_symbol_impact(symbol, direction=direction, max_depth=max_depth)
    return json.dumps(impact, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    def _sig_handler(signum, frame):
        os._exit(0)

    try:
        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT, _sig_handler)
    except Exception:
        pass

    try:
        mcp.run(transport="stdio")
    except (KeyboardInterrupt, SystemExit):
        os._exit(0)


if __name__ == "__main__":
    main()
