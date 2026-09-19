## 📝 Pull Request Summary

<!-- Briefly explain what changes this PR introduces and why they are needed. -->

## 🎯 Target Subsystems
- [ ] Tier 0: Symbols & SCIP Graph (`cortex/symbols/`)
- [ ] Tier 1: Episodic Knowledge Graph (`cortex/episodic/`)
- [ ] Tier 2: OpenViking Dynamic Chunker (`cortex/viking/`)
- [ ] Tier 3: Dense Retrieval & Reranker (`cortex/retrieval/`)
- [ ] FastMCP Server & Tools (`cortex/server.py`)
- [ ] Interactive Web Dashboard (`cortex/dashboard/`)
- [ ] Antigravity Integration & Hooks (`integrations/antigravity/`, `scripts/hooks/`)
- [ ] Documentation & CI (`docs/`, `.github/`)

## 🔍 Changes Made
<!-- Bulleted list of specific changes made -->
- 
- 

## 🧪 Verification & Testing
<!-- Describe the tests you ran to verify these changes. -->
- [ ] `uv run python3 scripts/test_dynamic_viking.py`
- [ ] `uv run python3 scripts/test_ultimate_chunking.py`
- [ ] `uv run python3 scripts/test_tier2_languages.py`
- [ ] `uv run python3 scripts/test_scip_ingest.py`
- [ ] `uv run python3 scripts/test_markdown_vault_ingestion.py`
- [ ] `uv run python3 scripts/test_antigravity_hooks.py`
- [ ] `uv run python3 scripts/test_dashboard_enhancements.py`
- [ ] `uv run python3 scripts/test_plugin_manifest.py`

## 📋 Pre-Merge Checklist
- [ ] Code follows the guidelines in [CODING_STANDARDS.md](CODING_STANDARDS.md).
- [ ] No single file exceeds the **1,000-line budget**.
- [ ] Zero hardcoded machine paths (`/Users/...`, `/home/...`).
- [ ] All new functions include type annotations.
- [ ] No secrets or `.env` files are tracked.
