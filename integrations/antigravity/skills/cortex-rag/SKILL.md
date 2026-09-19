---
name: cortex-rag
description: >-
  Operational runbook and deep guide for querying the Cortex 4-tier dual-memory and RAG system.
  Activate whenever the user mentions Cortex, searching memory, persistent knowledge, codebase structure,
  re-indexing workspaces, or debugging Cortex services (Qdrant, FalkorDB, oMLX). Also activate when
  search_knowledge_and_memory returns empty, when services are offline, or when the user invokes /cortex-rag.
---

# Cortex dual-memory and RAG operational runbook

Cortex provides a unified, four-tier memory base that combines deterministic compiler ASTs, temporal knowledge graphs, hierarchical code maps, and cross-encoder reranked vector search.

```
Antigravity Agent ──stdio──► FastMCP Server (cortex-server)
                                   │
       ┌───────────────────────────┼──────────────────────────┐
       ▼                           ▼                          ▼
Tier 0 & Tier 1                 Tier 2                     Tier 3
   FalkorDB                     Viking                     Qdrant
 AST Symbols &              tree-sitter AST              Dense + BM25
  Temporal KG                  In-Process              Qwen3 Reranker
    :6379                                                  :6333
       └───────────────────────────┼──────────────────────────┘
                                   ▼
                            LangGraph Merge
                         → Structured Markdown
```

---

## The four knowledge tiers

When you call `search_knowledge_and_memory(prompt)`, Cortex aggregates 4 grounded tiers:

| Tier | Source | Contents | Authority |
|------|--------|----------|-----------|
| **Tier 0** | FalkorDB `cortex_symbols` | Deterministic Symbol Declarations, Imports, Callers, and Markdown `[[WikiLinks]]` / Backlinks | **Ground Truth** |
| **Tier 1** | FalkorDB `cortex` | Temporal Episodic Facts & Architectural Decisions (Agent-Native FalkorDB Driver) | **Highest** |
| **Tier 2** | Viking Chunker | Hierarchical Code Map & AST Summaries (Module > Class > Function) | High |
| **Tier 3** | Qdrant + Qwen3 Reranker | Dense embeddings + BM25 lexical matches reranked by Qwen3-Reranker-4B | Medium (verify against live files) |

---

## Operating protocols

### 1. Before writing or modifying code
Always run:
```python
search_knowledge_and_memory(prompt="<task or architectural concept>")
```
Incorporate the returned symbols, historical decisions, and code snippets into your plan before calling `write_to_file` or `replace_file_content`.

### 2. Before refactoring or modifying signatures
Always run:
```python
trace_symbol_impact(symbol="<function_or_class_name>", direction="upstream")
```
This queries the FalkorDB symbol graph across all files to detect all callers, imports, and references that could break.

### 3. After completing significant work
Always run:
```python
record_system_outcome(
    episode_name="<Short 5-10 word summary>",
    content="<Detailed markdown summary of changes and decisions>",
    tags=["tag1", "tag2"],
    project="<project_name>",
    entities=[{"name": "...", "type": "...", "summary": "..."}],
    facts=[{"source": "...", "relation": "...", "target": "...", "fact": "..."}],
    supersedes=["<optional pattern of old facts to supersede>"]
)
```

---

## Service management and troubleshooting

Backend services can run locally on Apple Silicon, Linux, or Windows with hardware acceleration, or with any OpenAI-compatible provider:

| Service | Endpoint | Role | Quick Health Check |
|---------|----------|------|--------------------|
| **oMLX** | `http://localhost:8000` | Embeddings, Reranking, direct local LLM | `curl -s http://localhost:8000/health` |
| **Qdrant** | `http://localhost:6333` | Vector database | `curl -s http://localhost:6333/healthz` |
| **FalkorDB** | `localhost:6379` | Graph database (Temporal KG + Symbols) | `nc -zv localhost 6379` |
| **Dashboard** | `http://localhost:8004` | Topology UI & Visual Graph | `curl -s http://localhost:8004/` |
| **FalkorDB UI** | `http://localhost:3000` | Cypher query browser | Open in browser |

### Common fixes

- **Services Offline**:
  ```bash
  cd cortex && make start
  ```
- **Inspect Service Status**:
  ```bash
  cd cortex && make status
  ```
- **Re-index a Project**:
  ```bash
  cd cortex && make ingest WORKSPACE_ROOT=/path/to/my-project
  ```
- **Trigger cognitive dream mode**:
  ```python
  trigger_dream_mode(project="cortex", purge_stale=True)
  ```
  *(Prunes superseded facts, computes Louvain graph clusters, and updates `journal/invariants.md`).*
