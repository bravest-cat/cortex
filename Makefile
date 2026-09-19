DOCKER       ?= docker
LLAMA_SERVER ?= llama-server
LLAMA_LIBS   ?= /usr/local/lib
CORTEX_DIR   := $(shell pwd)
LOG_DIR      := $(CORTEX_DIR)/logs
PYTHON       := uv run python3

# Read model paths from .env if present
ifneq (,$(wildcard .env))
  include .env
  export
endif

EMBED_MODEL  ?= Qwen/Qwen3-Embedding-8B-4bit-DWQ
RERANK_MODEL ?= Qwen/Qwen3-Reranker-4B-4bit-MLX

.PHONY: help setup start stop restart status logs ingest watch stop-watch dashboard sync-journal test _check_docker \
        languages-status enable-cpp enable-jvm enable-swift enable-csharp enable-all-languages disable-lang scip-ingest scip-auto clean-test-graphs

.DEFAULT_GOAL := help

# ── Help & Command Directory ──────────────────────────────────────────────
help:
	@echo ""
	@printf "\033[1;36m🧠 Cortex — Local Dual-Memory & Cognitive RAG System\033[0m\n"
	@printf "\033[0;34m========================================================\033[0m\n"
	@printf "Usage: \033[1;33mmake <command> [OPTIONS]\033[0m\n\n"
	@printf "\033[1;37mSERVICE LIFECYCLE:\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "start" "Start all background services (Docker, oMLX)"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "stop" "Stop all background services"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "restart" "Restart all background services (stop then start)"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "status" "Health check reporting status of all backend services"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "setup" "One-time setup (Docker check, dependencies, directories, .env)"
	@echo ""
	@printf "\033[1;37mAST LANGUAGE GRAMMARS:\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "languages-status" "Display active and inactive Tree-sitter language grammars"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "enable-cpp" "Enable C & C++ (.c, .h, .cpp, .hpp, .cc, .cxx) AST parsing"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "enable-jvm" "Enable Java & Kotlin (.java, .kt, .kts) AST parsing"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "enable-swift" "Enable Swift (.swift) AST parsing"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "enable-csharp" "Enable C# (.cs) AST parsing"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "enable-all-languages" "Enable all Tier 1 + Tier 2 language grammars"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "disable-lang" "Disable grammar (Usage: make disable-lang LANG=<name>)"
	@echo ""
	@printf "\033[1;37mINGESTION & CHUNKING:\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "ingest" "Run AST-aware incremental ingestion on a directory"
	@printf "                     \033[0;33mUsage: make ingest WORKSPACE_ROOT=<path> [COLLECTION=<name>] [FORCE=1]\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "scip-ingest" "Ingest compiler-grade SCIP index (index.scip) into FalkorDB"
	@printf "                     \033[0;33mUsage: make scip-ingest SCIP_FILE=<path> [GRAPH=<name>]\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "scip-auto" "Auto-detect project ecosystem and ingest SCIP index"
	@printf "                     \033[0;33mUsage: make scip-auto [WORKSPACE_ROOT=<path>] [GRAPH=<name>]\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "watch" "Start real-time incremental file watcher daemon"
	@printf "                     \033[0;33mUsage: make watch [WORKSPACE_ROOT=<path>] [COLLECTION=<name>]\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "stop-watch" "Stop the real-time file watcher daemon"
	@echo ""
	@printf "\033[1;37mMEMORY & OBSERVABILITY:\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "sync-journal" "Backfill markdown journal entries into FalkorDB Knowledge Graph"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "dashboard" "Launch and open the Cortex Web Dashboard (:8004)"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "logs" "Tail live logs across all Cortex services in logs/*.log"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "test" "Run automated test suite for chunking and symbol graphs"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "clean-test-graphs" "Purge dangling test/temporary graphs from FalkorDB"
	@echo ""
	@printf "\033[1;37mANTIGRAVITY ECOSYSTEM INTEGRATION:\033[0m\n"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "plugin-install" "Install/symlink Cortex Antigravity Plugin and hooks"
	@printf "  \033[1;32m%-18s\033[0m %s\n" "plugin-status" "Check installation status of Antigravity plugin and hooks"
	@echo ""
	@printf "\033[1;37mCOMMON EXAMPLES:\033[0m\n"
	@printf "  \033[0;33mmake start\033[0m                                # Boot Qdrant, FalkorDB, and oMLX\n"
	@printf "  \033[0;33mmake languages-status\033[0m                     # Check which languages are enabled\n"
	@printf "  \033[0;33mmake enable-cpp\033[0m                           # Toggle on C/C++ AST support\n"
	@printf "  \033[0;33mmake ingest WORKSPACE_ROOT=$$(pwd)\033[0m          # Index workspace using AST chunker\n"
	@printf "  \033[0;33mmake watch WORKSPACE_ROOT=$$(pwd)\033[0m           # Start real-time incremental file watcher\n"
	@printf "  \033[0;33mmake dashboard\033[0m                            # Open visual web UI on http://localhost:8004\n"
	@printf "  \033[0;33mmake clean-test-graphs\033[0m                   # Purge dangling test databases from FalkorDB\n"
	@echo ""

# ── One-time setup ─────────────────────────────────────────────────────────
setup:
	@echo "=== Cortex Setup ==="
	@$(DOCKER) info > /dev/null 2>&1 || (echo "❌ Docker daemon not running. Open Docker Desktop first." && exit 1)
	@uv sync
	@mkdir -p $(LOG_DIR) data/qdrant_storage data/falkordb_storage journal
	@cp -n .env.example .env 2>/dev/null || true
	@echo "✅ Setup complete. Edit .env if needed, then: make start"

# ── Start all services ─────────────────────────────────────────────────────
start: _check_docker
	@echo "=== Starting Cortex Backend Services ==="
	@mkdir -p $(LOG_DIR)

	@# Docker containers (Qdrant + FalkorDB)
	@$(DOCKER) compose up -d
	@echo "✅ Qdrant + FalkorDB containers starting..."

	@# oMLX server (serving embedding & reranking on :8000)
	@if lsof -i:8000 -t > /dev/null 2>&1; then \
		echo "ℹ️  oMLX server already running on :8000"; \
	else \
		omlx start; \
		echo "✅ oMLX server starting on :8000"; \
	fi

	@echo ""
	@echo "Services are starting. Wait ~5s then run: make status"

# ── Stop all services ──────────────────────────────────────────────────────
stop:
	@echo "=== Stopping Cortex Services ==="
	@$(DOCKER) compose down 2>/dev/null || true
	@pkill -f "cortex.watcher" 2>/dev/null || true
	@echo "✅ Cortex services stopped."

# ── Restart all services ───────────────────────────────────────────────────
restart: stop start

# ── Health status ──────────────────────────────────────────────────────────
status:
	@echo "=== Cortex Health Status ==="
	@lsof -i:6333 -t > /dev/null 2>&1 \
		&& echo "✅ Qdrant             :6333 RUNNING" \
		|| echo "❌ Qdrant             :6333 DOWN"
	@lsof -i:6379 -t > /dev/null 2>&1 \
		&& echo "✅ FalkorDB           :6379 RUNNING" \
		|| echo "❌ FalkorDB           :6379 DOWN"
	@lsof -i:3000 -t > /dev/null 2>&1 \
		&& echo "✅ FalkorDB Browser   :3000 RUNNING" \
		|| echo "❌ FalkorDB Browser   :3000 DOWN"
	@lsof -i:8000 -t > /dev/null 2>&1 \
		&& echo "✅ oMLX server        :8000 RUNNING (Embed + Rerank + LLM)" \
		|| echo "❌ oMLX server        :8000 DOWN"
	@lsof -i:8004 -t > /dev/null 2>&1 \
		&& echo "✅ Cortex Dashboard   :8004 RUNNING" \
		|| echo "❌ Cortex Dashboard   :8004 DOWN"

# ── Sync Journal -> FalkorDB ───────────────────────────────────────────────
sync-journal:
	@uv run python3 -m cortex.scripts.sync_journal

# ── Log tailing ────────────────────────────────────────────────────────────
logs:
	@tail -f $(LOG_DIR)/*.log

# ── Ingest a workspace ─────────────────────────────────────────────────────
ingest:
	@if [ -z "$(WORKSPACE_ROOT)" ]; then \
		echo "❌ Error: WORKSPACE_ROOT is required."; \
		echo "Usage: make ingest WORKSPACE_ROOT=<path> [COLLECTION=<name>] [FORCE=1]"; \
		exit 1; \
	fi
	@uv run cortex-ingest --root "$(WORKSPACE_ROOT)" \
		$(if $(COLLECTION),--collection "$(COLLECTION)",) \
		$(if $(filter 1 true,$(FORCE)),--force,)

# ── File watcher daemon ───────────────────────────────────────────────────
watch:
	@echo "Starting real-time Cortex Incremental File Watcher..."
	@uv run python3 -m cortex.watcher --root "$${WORKSPACE_ROOT:-.}" $$(if [ -n "$$COLLECTION" ]; then echo "--collection $$COLLECTION"; fi)

stop-watch:
	@pkill -f "cortex.watcher" 2>/dev/null || true
	@echo "✅ Cortex Watcher stopped."

# ── Cortex Web Dashboard ───────────────────────────────────────────────────
dashboard:
	@if lsof -i:8004 -t > /dev/null 2>&1; then \
		echo "✅ Cortex Dashboard is running on http://localhost:8004"; \
	else \
		launchctl load -w ~/Library/LaunchAgents/com.cortex.dashboard.plist 2>/dev/null || launchctl start com.cortex.dashboard || uv run python3 -m cortex.dashboard.app > $(LOG_DIR)/dashboard.log 2>&1 & \
		echo "✅ Cortex Dashboard started on http://localhost:8004"; \
	fi
	@open http://localhost:8004 2>/dev/null || true

# ── Run Test Suite ────────────────────────────────────────────────────────
test:
	@uv run python3 scripts/test_ultimate_chunking.py

# ── Language Grammars Management ──────────────────────────────────────────
languages-status:
	@uv run python3 -c "from cortex.chunking.ast_splitter import print_language_status; print_language_status()"

enable-cpp:
	@echo "Installing C / C++ Tree-sitter grammars..."
	@uv pip install tree-sitter-c tree-sitter-cpp
	@echo "✅ C & C++ (.c, .h, .cpp, .hpp, .cc, .cxx) enabled."
	@$(MAKE) languages-status

enable-jvm:
	@echo "Installing Java & Kotlin Tree-sitter grammars..."
	@uv pip install tree-sitter-java tree-sitter-kotlin
	@echo "✅ Java & Kotlin (.java, .kt, .kts) enabled."
	@$(MAKE) languages-status

enable-swift:
	@echo "Installing Swift Tree-sitter grammar..."
	@uv pip install tree-sitter-swift
	@echo "✅ Swift (.swift) enabled."
	@$(MAKE) languages-status

enable-csharp:
	@echo "Installing C# Tree-sitter grammar..."
	@uv pip install tree-sitter-c-sharp
	@echo "✅ C# (.cs) enabled."
	@$(MAKE) languages-status

enable-all-languages:
	@echo "Installing all Tier 2 Tree-sitter grammars..."
	@uv pip install tree-sitter-c tree-sitter-cpp tree-sitter-java tree-sitter-kotlin tree-sitter-swift tree-sitter-c-sharp
	@echo "✅ All Tier 2 language grammars enabled."
	@$(MAKE) languages-status

disable-lang:
	@if [ -z "$(LANG)" ]; then \
		echo "❌ Error: LANG is required. Example: make disable-lang LANG=swift"; \
		echo "Available: c, cpp, java, kotlin, swift, c-sharp"; \
		exit 1; \
	fi
	@uv pip uninstall tree-sitter-$(LANG)
	@echo "✅ Uninstalled tree-sitter-$(LANG)."
	@$(MAKE) languages-status

# ── SCIP Code Intelligence ────────────────────────────────────────────────
scip-ingest:
	@if [ -z "$(SCIP_FILE)" ]; then \
		echo "❌ Error: SCIP_FILE is required. Example: make scip-ingest SCIP_FILE=index.scip [GRAPH=cortex_symbols]"; \
		exit 1; \
	fi
	@echo "Ingesting SCIP index from $(SCIP_FILE)..."
	@$(PYTHON) -m cortex.symbols.scip $(SCIP_FILE) $(if $(GRAPH),--graph $(GRAPH),)

scip-auto:
	@echo "Running SCIP auto-detection and ingestion..."
	@$(PYTHON) -m cortex.symbols.scip --auto "$(or $(WORKSPACE_ROOT),.)" $(if $(GRAPH),--graph $(GRAPH),)

clean-test-graphs:
	@echo "Purging lingering test graphs from FalkorDB..."
	@$(PYTHON) -c "import redis; r = redis.Redis(host='localhost', port=6379, decode_responses=True); \
		graphs = [g for g in r.execute_command('GRAPH.LIST') if 'test' in g.lower()]; \
		[r.execute_command('GRAPH.DELETE', g) for g in graphs]; \
		print(f'✅ Deleted {len(graphs)} test graph(s): {graphs}')"

# ── Antigravity Plugin Integration ────────────────────────────────────────
plugin-install:
	@$(PYTHON) scripts/install_plugin.py

plugin-status:
	@echo "=== Cortex Antigravity Integration Status ==="
	@if [ -L ~/.gemini/antigravity-cli/plugins/cortex ]; then \
		echo "  CLI Plugin:     ✅ ACTIVE (~/.gemini/antigravity-cli/plugins/cortex)"; \
	else \
		echo "  CLI Plugin:     ❌ NOT INSTALLED"; \
	fi
	@if [ -L ~/.gemini/config/plugins/cortex ]; then \
		echo "  Global Plugin:  ✅ ACTIVE (~/.gemini/config/plugins/cortex)"; \
	else \
		echo "  Global Plugin:  ❌ NOT INSTALLED"; \
	fi
	@echo "  Hooks Suite:    ✅ 4 hooks in scripts/hooks/"
	@echo "  Skills:         ✅ cortex-rag, cortex-architect"
	@echo "  Subagents:      ✅ cortex-researcher, cortex-architect"
	@echo "  Statusline:     ✅ integrations/antigravity/cortex_statusline.py"

# ── Internal helpers ───────────────────────────────────────────────────────
_check_docker:
	@$(DOCKER) info > /dev/null 2>&1 || (echo "❌ Docker daemon not running. Open Docker Desktop." && exit 1)

