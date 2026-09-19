# Contributing to Cortex

This document covers local setup, contribution workflows, and coding rules for Cortex.

---

## Code of conduct

Contributors must follow the [Code of conduct](CODE_OF_CONDUCT.md). Report issues to project maintainers.

---

## Local development setup

Cortex requires Python 3.11 or newer, Docker, and `uv`.

### 1. Initial setup
```bash
cd cortex
make setup
make start
make status
```

### 2. Environment variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Update `EMBED_BASE_URL` and `EMBED_MODEL_NAME` to match your local or remote inference service (such as Ollama, vLLM, TEI, oMLX, or OpenAI).

---

## Automated testing

All 8 test suites must pass before submitting a pull request:

```bash
make test
```

To run individual test suites:
```bash
uv run python3 scripts/test_dynamic_viking.py           # OpenViking dynamic watching & L0/L1 sync
uv run python3 scripts/test_ultimate_chunking.py        # Hierarchical AST parsing & preambles
uv run python3 scripts/test_tier2_languages.py         # 10 Tree-sitter language grammars
uv run python3 scripts/test_scip_ingest.py              # SCIP Protobuf index deserialization
uv run python3 scripts/test_markdown_vault_ingestion.py # Obsidian WikiLinks & backlinks
uv run python3 scripts/test_antigravity_hooks.py        # Antigravity lifecycle hooks
uv run python3 scripts/test_dashboard_enhancements.py   # Web dashboard API endpoints
uv run python3 scripts/test_plugin_manifest.py         # Antigravity plugin manifest schemas
```

---

## Architecture invariants and standards

1. **File length ceiling**: Source files must not exceed 1,000 lines of code. Split modules that approach this limit into domain submodules.
2. **Deterministic typing**: Functions and methods must include explicit type annotations.
3. **No hardcoded paths**: Do not commit machine-specific paths (`/Users/...`, `/home/...`). Resolve paths relative to the file location via `Path(__file__).resolve()`.
4. **Four-tier memory precedence**: Respect tier authority. Tier 0 AST symbols take precedence over Tier 1 episodic facts, Tier 2 OpenViking signatures, and Tier 3 vector text chunks.
5. **Universal hardware support**: Do not bind code to a specific accelerator or operating system. Use OpenAI-compatible endpoints with configurable vector dimensions.
6. **Reference standards**: See [Coding standards](CODING_STANDARDS.md) for style and implementation rules.

---

## Git workflow and pull requests

### 1. Branch naming
Use descriptive prefixes for branches:
- `feat/name` for new features
- `fix/name` for bug fixes
- `perf/name` for performance improvements
- `docs/name` for documentation
- `refactor/name` for refactoring

### 2. Commit message format
Use the Conventional Commits format:
```
<type>(<optional scope>): <description>

[optional body]

[optional footer]
```

Examples:
- `feat(viking): add incremental delete_file cache invalidation`
- `fix(ingest): support dynamic EMBED_DIMENSION in vector parameters`
- `docs(operations): add Ollama and vLLM configuration instructions`

### 3. Pull request checklist
1. Verify that `make test` exits with code 0.
2. Confirm that `.env`, test logs, and temporary databases remain untracked.
3. Submit a pull request against `main` using the [Pull request template](.github/PULL_REQUEST_TEMPLATE.md).
4. Include test commands and output in the description.

---

## Reporting issues and feature requests

- **Bug reports**: Use the [Bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include operating system, hardware details, logs, and reproduction steps.
- **Feature requests**: Use the [Feature request template](.github/ISSUE_TEMPLATE/feature_request.md). Describe the use case and proposed design.
