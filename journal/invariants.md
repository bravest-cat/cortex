
## Updated: 2026-09-18

### Invariant [CODE CHUNKING & INGESTION]
**Rule:** All code chunks must be generated via AST-aware hierarchical splitting (Tree-sitter) with deterministic token-bounded sizing (~384 tokens) and injected contextual preambles (File > Scope > Signature) to preserve semantic hierarchy.
**Rationale:** Naive sentence splitting fails to respect code structure (e.g., breaking loops or classes), leading to hallucinated context; AST-based splitting with strict token budgets ensures retrieval units are logically coherent and fit within LLM context windows.

### Invariant [RETRIEVAL ARCHITECTURE]
**Rule:** Knowledge retrieval must use a hybrid dense-sparse fusion strategy (Vector + BM25) followed by cross-encoder reranking, with support for multi-collection federation to query disparate memory tiers (Episodic vs. Symbolic) simultaneously.
**Rationale:** Single-mode retrieval (pure vectors or pure keywords) lacks precision and recall balance; hybrid fusion with RRF mitigates sparse vector noise, while cross-encoder reranking ensures high-fidelity context selection across federated graph and vector stores.

### Invariant [SYMBOLIC MEMORY & GRAPH INTEGRITY]
**Rule:** Codebase topology must be maintained in a separate, deterministic FalkorDB symbol graph (cortex_symbols) derived from exact Tree-sitter AST parsing, isolated from episodic decision logs to prevent graph pollution by built-in language constructs.
**Rationale:** Mixing external library calls (e.g., len, print) with project-specific logic corrupts architectural impact analysis; separating deterministic symbol graphs from episodic memory ensures accurate dependency tracing and clean visualization.

### Invariant [SYSTEM STABILITY & PORTABILITY]
**Rule:** All local LLM and embedding services must be daemonized via macOS LaunchAgents with automatic crash recovery, and GPU execution parameters (batch size, concurrency) must be strictly bounded to prevent ARM64 deadlocks.
**Rationale:** Local inference services are prone to OOM crashes and GPU hangs on Apple Silicon; explicit process supervision ensures continuous operation, while hardware-specific tuning guarantees stability across diverse local environments.
