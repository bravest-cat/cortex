---
name: cortex-architect
description: >-
  System architecture, refactoring, and blast-radius analysis runbook for Cortex.
  Activate whenever the user mentions refactoring code, renaming or deleting symbols, restructuring
  codebases, analyzing blast radius, auditing architectural invariants, or running Louvain community
  clustering. Also activate when planning non-trivial changes or when the user invokes /cortex-architect.
---

# Cortex system architect and blast radius runbook

When modifying shared interfaces, renaming symbols, restructuring directories, or planning architectural refactors, follow this deterministic five-step runbook to guarantee zero regression.

---

## Five-step refactoring runbook

### Step 1: Upstream blast radius analysis
Before changing a symbol signature, deleting a method, or renaming a class, discover all dependent files and call sites:
```python
trace_symbol_impact(
    symbol="<SymbolName>",
    direction="upstream",
    max_depth=3
)
```
- **`upstream`**: Identifies all files and functions that call or import this symbol (these will break if the signature changes).
- **`downstream`**: Identifies all dependencies and services this symbol relies on.

### Step 2: Query historical decisions
Check why the current architecture exists and whether prior decisions constrain this refactor:
```python
search_knowledge_and_memory(prompt="Why was <Component> structured this way?")
```
Review:
1. Operational Facts & Decisions (FalkorDB Temporal KG)
2. Active Invariants in `journal/invariants.md`

### Step 3: Execute incremental code changes
Modify files using `replace_file_content` or `write_to_file`.
- Thanks to the `PostToolUse` lifecycle hook, every saved file is automatically hot-synced into FalkorDB and Qdrant in <150ms.
- Run local unit tests to verify behavior:
  ```bash
  pytest <test_file>
  ```

### Step 4: Record outcome and supersede stale facts
After testing and verifying changes, record the new architecture state:
```python
record_system_outcome(
    episode_name="Refactored <Component> to <NewPattern>",
    content="Detailed explanation of the new architecture, interfaces, and changes.",
    tags=["refactor", "architecture", "<component>"],
    project="cortex",
    entities=[{"name": "<Component>", "type": "module", "summary": "New architecture description"}],
    facts=[{"source": "<Component>", "relation": "IMPLEMENTS", "target": "<Pattern>", "fact": "..."}],
    supersedes=["<old fact pattern to supersede>"]
)
```

### Step 5: Trigger cognitive dream mode
Consolidate memory, purge superseded facts, recompute Louvain subsystem clusters, and induce updated invariants:
```python
trigger_dream_mode(project="cortex", purge_stale=True)
```
This writes a dated audit log in `journal/dreams/` and updates `journal/invariants.md`.
