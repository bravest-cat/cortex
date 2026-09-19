# Operations, setup, and troubleshooting

This document covers system requirements, service commands, model configurations, and troubleshooting steps for Cortex.

---

## 1. System requirements

Cortex runs on Linux, macOS, and Windows:
- **Operating systems**: macOS (Apple Silicon and Intel), Linux (Ubuntu, Debian, Fedora, Arch), Windows via WSL2.
- **Accelerators**:
  - NVIDIA GPUs: CUDA via vLLM, Ollama, or Hugging Face Text Embeddings Inference (TEI).
  - AMD GPUs: ROCm via vLLM or Ollama.
  - Apple Silicon: Metal via oMLX, LM Studio, or Ollama.
  - CPUs: Standard x86_64 and ARM64 via llama.cpp, Ollama, or cloud endpoints.
- **Python**: 3.11 or 3.12 managed with `uv`.
- **Containers**: Docker Desktop, Docker Engine, or Podman (runs FalkorDB and Qdrant).
- **RAM / VRAM**: 8 GB minimum for compact models; 16 GB or more for 8B models.

---

## 2. Services

| Service | Port | Container / Process | Purpose | Verification command |
| :--- | :--- | :--- | :--- | :--- |
| FalkorDB | 6379 | Docker (`falkordb/falkordb:latest`) | Graph database for episodic facts and symbols | `nc -zv localhost 6379` |
| Qdrant | 6333 | Docker (`qdrant/qdrant:latest`) | Vector database for text chunks | `curl -s http://localhost:6333/healthz` |
| FalkorDB UI | 3000 | Docker (`falkordb/falkordb-browser`) | Cypher query browser | Open `http://localhost:3000` |
| Inference server | User set | Ollama, vLLM, TEI, oMLX, or cloud API | Embeddings and optional reranking | `curl -s $EMBED_BASE_URL/models` |
| Dashboard | 8004 | Python / FastAPI | Web visualization and impact explorer | `curl -s http://localhost:8004/` |

---

## 3. Makefile commands

```bash
# Setup and service management
make setup            # Pulls Docker images, creates virtual environment via uv, and installs dependencies
make start            # Starts FalkorDB and Qdrant containers
make stop             # Stops all Docker containers
make restart          # Restarts containers and clears temporary caches
make status           # Checks health of all running services

# Ingestion
make ingest           # Indexes current workspace into FalkorDB and Qdrant
make ingest WORKSPACE_ROOT=/path/to/project  # Indexes a specific path

# Antigravity integration
make plugin-install   # Generates manifests and links plugin to Antigravity CLI
make test             # Runs all 8 test suites

# UI and tools
make dashboard        # Starts the web dashboard on http://localhost:8004
make dream            # Runs memory consolidation from the command line
```

---

## 4. Inference configurations

Cortex connects to any OpenAI-compatible embedding or reranking server:

### Ollama (NVIDIA, AMD, Apple Silicon, CPU)
```bash
ollama pull nomic-embed-text
```
Configure in `.env`:
```env
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_MODEL_NAME=nomic-embed-text
EMBED_DIMENSION=768
EMBED_API_KEY=not-needed
```

### vLLM or TEI on Linux / NVIDIA
```bash
docker run --gpus all -p 8000:80 ghcr.io/huggingface/text-embeddings-inference:latest \
  --model-id BAAI/bge-m3
```
Configure in `.env`:
```env
EMBED_BASE_URL=http://localhost:8000/v1
EMBED_MODEL_NAME=BAAI/bge-m3
EMBED_DIMENSION=1024
```

### Apple Silicon via oMLX
```bash
uv run omlx --port 8000 --model mlx-community/Qwen3-Embedding-8B-4bit-DWQ
```
Configure in `.env`:
```env
EMBED_BASE_URL=http://localhost:8000/v1
EMBED_MODEL_NAME=mlx-community--Qwen3-Embedding-8B-4bit-DWQ
EMBED_DIMENSION=4096
```

### Cloud providers (OpenAI, Azure, Cohere)
```env
EMBED_BASE_URL=https://api.openai.com/v1
EMBED_MODEL_NAME=text-embedding-3-small
EMBED_DIMENSION=1536
EMBED_API_KEY=sk-...

# Optional Cohere reranker:
RERANK_BASE_URL=https://api.cohere.com/v1
RERANK_MODEL_NAME=rerank-v3.5
RERANK_API_KEY=...
```

If no reranker is configured, Cortex ranks results by dense vector score.

---

## 5. Troubleshooting

### Databases do not respond
```bash
# Check Docker status
docker ps

# Restart containers
make restart

# Verify ports
nc -zv localhost 6379
curl http://localhost:6333/healthz
```

### Inference server unreachable
```bash
# Test endpoint
curl -s $EMBED_BASE_URL/models
```

### Antigravity hooks do not fire
```bash
# Reinstall plugin manifests
make plugin-install

# Check generated files
cat ~/.gemini/antigravity-cli/plugins/cortex/hooks.json
cat ~/.gemini/antigravity-cli/plugins/cortex/sidecars/cortex-watcher/sidecar.json
```

### Run diagnostic test suite
```bash
make test
```
