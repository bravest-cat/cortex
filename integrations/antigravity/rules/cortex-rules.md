# Cortex agent behavioral rules

These rules govern how AI coding agents interact with the Cortex dual-memory and RAG architecture.

---

## Core rules

### 1. Retrieve before deciding or modifying
You must ALWAYS call `search_knowledge_and_memory(prompt)` before:
- Writing new code or editing existing code in the project workspace
- Proposing architectural changes or choosing third-party libraries
- Debugging recurring failures or answering codebase structure questions

**Context Tiers**:
- **Tier 0 (FalkorDB Symbols & Backlinks)**: Exact declarations, imports, callers, and WikiLinks.
- **Tier 1 (Temporal Knowledge Graph)**: Historical decisions and operational facts (highest authority).
- **Tier 2 (Viking AST Map)**: Hierarchical module/class/function structure.
- **Tier 3 (Qdrant + Qwen3 Reranker)**: Dense + lexical code snippets.

---

### 2. Verify blast radius before refactoring
Before modifying a function signature, renaming a method, or deleting a class:
- Call `trace_symbol_impact(symbol="<name>", direction="upstream")`.
- Audit all callers and imported references returned by the graph.
- Update all call sites across all impacted files to prevent broken references.

---

### 3. Persist outcomes after modifying
After completing any non-trivial task, modifying configuration, or refactoring code:
- Call `record_system_outcome(...)` with:
  - `episode_name`: 5-10 word descriptive summary
  - `content`: Markdown explanation of decisions, rationale, and verified tests
  - `tags`: Lowercase keywords
  - `project`: Project label (e.g. `cortex`)
  - `entities`: Structured entities list
  - `facts`: Triples representing new relations
  - `supersedes`: Pattern matching any obsolete facts

---

## Service endpoints reference
- **oMLX**: `http://localhost:8000/v1` (Embeddings: `Qwen3-Embedding-8B-4bit-DWQ`, Reranking: `Qwen3-Reranker-4B-4bit-MLX`)
- **Qdrant**: `http://localhost:6333` (Collection: `cortex_codebase`)
- **FalkorDB**: `localhost:6379` (Graphs: `cortex`, `cortex_symbols`)
- **Dashboard**: `http://localhost:8004`
- **Restart command**: `make start` (from Cortex root)
