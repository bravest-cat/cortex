# Coding standards and guidelines

This document defines code quality, typing, performance, and architectural rules for the Cortex codebase.

---

## 1. Core principles

1. **Deterministic ground truth first**: Code definitions and compiler-grade ASTs (Tier 0) take precedence over vector embeddings (Tier 3).
2. **Universal compatibility**: Code must run across Linux, macOS, and Windows with any OpenAI-compatible model provider and hardware accelerator.
3. **Sub-5ms hot-sync**: File updates must refresh in-memory context in under 5ms without blocking agent workflows or rescanning the workspace.
4. **Decoupled layers**: Keep databases, retrieval logic, AST extractors, and MCP transport separated behind clear interfaces.

---

## 2. Python standards and style

### Formatting
- Follow PEP 8 conventions.
- Line length: Keep lines under 100 characters where practical. Docstrings may break naturally.
- Imports: Group into standard library, third-party packages, and local Cortex modules. Sort alphabetically within groups.

### Type annotations
- Include type annotations on all function, method, and class definitions.
- Use standard Python typing syntax (`list[str]`, `dict[str, Any]`, `X | None`).
- Avoid unconstrained `Any`. Use dataclasses, Pydantic models, or `TypedDict` for structured dictionaries.

```python
# Good: Explicit typing
def trace_impact(
    self,
    symbol_name: str,
    direction: Literal["upstream", "downstream"] = "upstream",
    max_depth: int = 3,
) -> ImpactReport:
    ...

# Bad: Untyped parameters
def trace_impact(self, symbol, direction="upstream", max_depth=3):
    ...
```

### File size ceiling
- Files must stay under 1,000 lines of code.
- When a module exceeds 850 lines, split it into cohesive domain submodules (such as `chunking/node_parser.py`, `chunking/skeleton.py`, and `chunking/languages.py`).

---

## 3. Database and retrieval invariants

### FalkorDB (episodic and symbol graphs)
- Always use parameterized Cypher queries to prevent injection:
  ```python
  # Good: Parameterized query
  query = "MATCH (s:Symbol {name: $name}) RETURN s"
  client.query(query, {"name": symbol_name})
  
  # Bad: String interpolation
  query = f"MATCH (s:Symbol {{name: '{symbol_name}'}}) RETURN s"
  ```
- Do not delete historical facts. Link newer conflicting facts using `[:SUPERSEDES]` edges with `VALID_UNTIL` timestamps.

### Qdrant (dense vectors and sparse BM25)
- Read vector sizes from `EMBED_DIMENSION`. Do not hardcode a single dimension.
- If a cross-encoder reranker endpoint fails or times out, catch the error and fall back to dense vector scores without exiting.

---

## 4. AST and Tree-sitter parsers

1. **Parser resilience**: Tree-sitter traversals must handle incomplete or invalid syntax without throwing unhandled exceptions.
2. **Canonical language mapping**: Route file extensions through `CANONICAL_LANG_MAP` in `cortex/chunking/languages.py`.
3. **Skeleton preservation**: When extracting skeleton chunks, retain docstrings, annotations, and decorators while stripping method bodies.

---

## 5. Security and path handling

1. **No hardcoded user paths**:
   - Do not write `/Users/...` or `/home/...` into code.
   - Resolve paths relative to the file location:
     ```python
     CORTEX_ROOT = Path(__file__).resolve().parent.parent
     ```
2. **Environment secrets**:
   - Keep secret tokens in `.env`.
   - Never commit `.env` files. Document all variables in `.env.example`.

---

## 6. Testing requirements

1. **Test coverage for new features**:
   - New FastMCP tool: Add tests in `scripts/test_*.py` checking arguments and responses.
   - New language parser: Add tests in `scripts/test_tier2_languages.py`.
   - New chunking strategy: Add tests in `scripts/test_ultimate_chunking.py`.
2. **Idempotence**: Tests must clean up their test collections and graphs when finished.
3. **CI compatibility**: Tests must run against standard containerized FalkorDB and Qdrant instances.
