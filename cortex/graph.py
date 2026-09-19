"""
LangGraph state machine orchestrator.

Flow: parallel_fetch → synthesize → END

parallel_fetch runs three retrieval nodes concurrently:
  - Node A (Episodic)  : Graphiti temporal facts
  - Node B (Viking)    : tree-sitter structural context
  - Node C (Factual)   : Qdrant + reranker code snippets

synthesize merges all results into a structured Markdown block.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END

from cortex.retrieval import RetrievalEngine
from cortex.viking import VikingChunker
from cortex.episodic import EpisodicMemory
from cortex.symbols.graph import SymbolGraphManager


# ── Typed state ────────────────────────────────────────────────────────────

class CortexState(TypedDict, total=False):
    query: str
    collection: Optional[str]
    workspace_root: Optional[str]
    kg_facts: List[str]
    viking_summaries: str
    doc_snippets: List[str]
    symbol_results: List[Dict[str, Any]]
    final_context: str


def _extract_symbol_candidates(query: str) -> List[str]:
    """Extracts likely symbol identifiers from a natural language query."""
    candidates = []
    # 1. Backtick quotes: `SymbolName`
    candidates.extend(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", query))

    # 2. Intent prefixes: "where is X", "def X", "function X", "references to X", "class X"
    intent_matches = re.findall(
        r"(?:where is|what is|find|class|def|function|method|symbol|references to|callers of|calls to)\s+([A-Za-z_][A-Za-z0-9_]*)",
        query,
        flags=re.IGNORECASE,
    )
    candidates.extend(intent_matches)

    # 3. Call syntax: foo()
    call_matches = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", query)
    candidates.extend(call_matches)

    # 4. CamelCase identifiers: e.g. SymbolGraphManager, ASTCodeSplitter
    camel_matches = re.findall(r"\b[A-Z][a-zA-Z0-9]*[a-z][A-Za-z0-9]*\b", query)
    candidates.extend(camel_matches)

    # 5. Snake_case identifiers: e.g. extract_symbols_and_references
    snake_matches = re.findall(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b", query)
    candidates.extend(snake_matches)

    # 6. Single identifier query
    clean_q = query.strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", clean_q):
        candidates.append(clean_q)

    stop_words = {
        "is", "where", "what", "find", "class", "def", "function", "method", "symbol",
        "references", "to", "of", "in", "and", "or", "not", "for", "the", "a", "an",
        "self", "true", "false", "none", "return", "import", "from", "async", "await",
        "query", "test", "code", "file", "line",
    }
    seen = set()
    result = []
    for c in candidates:
        low = c.lower()
        if low not in stop_words and c not in seen and len(c) >= 3:
            seen.add(c)
            result.append(c)
            if len(result) >= 5:
                break
    return result


# ── Graph factory ──────────────────────────────────────────────────────────

def make_graph(
    retrieval: RetrievalEngine,
    viking: VikingChunker,
    episodic: EpisodicMemory,
    symbol_graph: Optional[SymbolGraphManager] = None,
):
    """Build and compile the LangGraph pipeline."""
    if symbol_graph is None:
        symbol_graph = SymbolGraphManager()

    async def parallel_fetch(state: CortexState) -> dict:
        """Run all four fetch operations concurrently."""

        async def fetch_episodic():
            try:
                return await episodic.search(state["query"])
            except Exception as e:
                print(f"[Cortex/Episodic] Error: {e}")
                return []

        async def fetch_viking():
            try:
                ws = state.get("workspace_root")
                return viking.get_context_for_query(state["query"], workspace_root=ws)
            except Exception as e:
                print(f"[Cortex/Viking] Error: {e}")
                return ""

        async def fetch_retrieval():
            try:
                target_col = state.get("collection")
                return await retrieval.query(state["query"], collection=target_col)
            except Exception as e:
                print(f"[Cortex/Retrieval] Error: {e}")
                return []

        async def fetch_symbols():
            if not symbol_graph:
                return []
            candidates = _extract_symbol_candidates(state["query"])
            if not candidates:
                return []
            loop = asyncio.get_running_loop()

            async def _lookup(cand: str):
                try:
                    return await loop.run_in_executor(None, symbol_graph.find_references, cand)
                except Exception as e:
                    print(f"[Cortex/Symbols] Error resolving '{cand}': {e}")
                    return None

            results = await asyncio.gather(*[_lookup(c) for c in candidates])
            return [
                r for r in results
                if r and isinstance(r, dict) and (r.get("definitions") or r.get("references") or r.get("imports"))
            ]

        kg_facts, viking_summaries, doc_snippets, symbol_results = await asyncio.gather(
            fetch_episodic(),
            fetch_viking(),
            fetch_retrieval(),
            fetch_symbols(),
        )
        return {
            "kg_facts": kg_facts,
            "viking_summaries": viking_summaries,
            "doc_snippets": doc_snippets,
            "symbol_results": symbol_results,
        }

    async def synthesize(state: CortexState) -> dict:
        """Merge all sources into structured Markdown blocks."""
        facts_md = (
            "\n".join(f"- {f}" for f in state["kg_facts"])
            if state["kg_facts"]
            else "_No relevant operational facts found._"
        )
        snippets_md = (
            "\n\n".join(f"```\n{s}\n```" for s in state["doc_snippets"])
            if state["doc_snippets"]
            else "_No code references found._"
        )
        viking_md = state["viking_summaries"] or "_No structural context available._"

        # Format deterministic code symbols
        symbol_results = state.get("symbol_results") or []
        symbol_section = ""
        if symbol_results:
            blocks = []
            for item in symbol_results:
                sym = item.get("symbol", "")
                defs = item.get("definitions", [])
                imports = item.get("imports", [])
                refs = item.get("references", [])

                lines = [f"- **`{sym}`**"]
                if defs:
                    for d in defs:
                        fp = d.get("file_path", "")
                        ln = d.get("line", 1)
                        kind = d.get("kind", "symbol")
                        sig = d.get("signature", "")
                        doc = d.get("docstring", "")
                        parent = d.get("parent")
                        parent_info = f" in `{parent}`" if parent else ""
                        lines.append(f"  - **Definition ({kind}{parent_info})**: [{fp}:{ln}](file://{fp}#L{ln})")
                        if sig:
                            lines.append(f"    - Signature: `{sig}`")
                        if doc:
                            lines.append(f"    - Docstring: {doc}")
                else:
                    lines.append("  - **Definition**: _External or unindexed declaration_")

                if imports:
                    lines.append(f"  - **Imported by ({len(imports)} file(s))**:")
                    for imp in imports[:8]:
                        fp = imp.get("file_path", "")
                        ln = imp.get("line", 1)
                        mod = imp.get("module") or ""
                        mod_info = f" (from `{mod}`)" if mod else ""
                        lines.append(f"    - [{fp}:{ln}](file://{fp}#L{ln}){mod_info}")
                    if len(imports) > 8:
                        lines.append(f"    - _... and {len(imports) - 8} more files_")

                if refs:
                    lines.append(f"  - **Cross-File References & Calls ({len(refs)} site(s))**:")
                    for r in refs[:10]:
                        fp = r.get("file_path", "")
                        ln = r.get("line", 1)
                        kind = r.get("kind", "call")
                        lines.append(f"    - [{fp}:{ln}](file://{fp}#L{ln}) ({kind})")
                    if len(refs) > 10:
                        lines.append(f"    - _... and {len(refs) - 10} more call sites_")

                backlinks = item.get("backlinks", [])
                if backlinks:
                    lines.append(f"  - **Referenced in Documentation Notes ({len(backlinks)} note(s))**:")
                    for bl in backlinks[:8]:
                        fp = bl.get("file_path", "")
                        ln = bl.get("line", 1)
                        doc_t = bl.get("doc_title") or Path(fp).name
                        lbl = bl.get("label", "")
                        ltype = bl.get("link_type", "wiki_link")
                        lbl_info = f" (via `{ltype}`: \"{lbl}\")" if lbl else ""
                        lines.append(f"    - [{doc_t} ({fp}:{ln})](file://{fp}#L{ln}){lbl_info}")
                    if len(backlinks) > 8:
                        lines.append(f"    - _... and {len(backlinks) - 8} more note links_")

                blocks.append("\n".join(lines))

            symbol_section = f"""### Deterministic Code Symbols & Knowledge Graph References (FalkorDB)
{chr(10).join(blocks)}

"""

        final_context = f"""{symbol_section}### Operational Facts & Decisions (Graphiti)
{facts_md}

### System Architecture Overview (OpenViking)
{viking_md}

### Ground-Truth Code References (Qdrant Reranked)
{snippets_md}"""

        return {"final_context": final_context}

    # Build graph
    g = StateGraph(CortexState)
    g.add_node("parallel_fetch", parallel_fetch)
    g.add_node("synthesize", synthesize)
    g.set_entry_point("parallel_fetch")
    g.add_edge("parallel_fetch", "synthesize")
    g.add_edge("synthesize", END)

    return g.compile()
