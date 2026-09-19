"""
Two-stage hybrid retrieval pipeline:
  Stage 1: Qdrant Hybrid Search (Dense Qwen3 vectors + Sparse BM25 vectors via RRF)
  Stage 2: Qwen3 cross-encoder rerank (narrow to top RERANK_TOP_K)

Supports dynamic collection routing and automatic fallback for legacy collections.
"""
import os
from typing import Dict, List, Optional

import httpx
from dotenv import load_dotenv
from llama_index.core import Settings, VectorStoreIndex
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client import models as qmodels

load_dotenv()

EMBED_BASE_URL  = os.getenv("EMBED_BASE_URL", "http://localhost:8000/v1")
RERANK_BASE_URL = os.getenv("RERANK_BASE_URL", "http://localhost:8000/v1")
QDRANT_URL      = os.getenv("QDRANT_URL", "http://localhost:6333")
DEFAULT_COLLECTION = os.getenv("QDRANT_COLLECTION", "cortex_codebase")
TOP_K_RETRIEVE  = int(os.getenv("RETRIEVE_TOP_K", "30"))
TOP_K_RERANK    = int(os.getenv("RERANK_TOP_K", "5"))


class RetrievalEngine:
    """Manages Qdrant index lifecycle and runs two-stage hybrid retrieval."""

    def __init__(self):
        self._client: AsyncQdrantClient | None = None
        self._sync_client: QdrantClient | None = None
        self._indices: Dict[str, VectorStoreIndex] = {}
        self._hybrid_support: Dict[str, bool] = {}

    async def initialize(self):
        embed_api_key = os.getenv("EMBED_API_KEY") or os.getenv("OPENAI_API_KEY") or "not-needed"
        embed_model = OpenAIEmbedding(
            api_base=EMBED_BASE_URL,
            api_key=embed_api_key,
            model_name=os.getenv("EMBED_MODEL_NAME", "mlx-community--Qwen3-Embedding-8B-4bit-DWQ"),
            timeout=120.0,
            max_retries=3,
        )
        Settings.embed_model = embed_model
        Settings.chunk_size = 512
        Settings.chunk_overlap = 64

        self._sync_client = QdrantClient(url=QDRANT_URL)
        self._client = AsyncQdrantClient(url=QDRANT_URL)

        # Pre-initialize default collection
        await self._get_or_create_index(DEFAULT_COLLECTION)

    async def _get_or_create_index(self, collection_name: str) -> VectorStoreIndex:
        if collection_name in self._indices:
            return self._indices[collection_name]

        embed_dim = int(os.getenv("EMBED_DIMENSION", "4096"))
        if not self._sync_client.collection_exists(collection_name):
            self._sync_client.create_collection(
                collection_name=collection_name,
                vectors_config={"text-dense": qmodels.VectorParams(size=embed_dim, distance=qmodels.Distance.COSINE)},
                sparse_vectors_config={"text-sparse-new": qmodels.SparseVectorParams()},
            )
            is_hybrid = True
        else:
            col_info = self._sync_client.get_collection(collection_name)
            is_hybrid = (
                col_info.config.params.sparse_vectors is not None
                and "text-sparse-new" in col_info.config.params.sparse_vectors
            )

        self._hybrid_support[collection_name] = is_hybrid

        store = QdrantVectorStore(
            client=self._sync_client,
            collection_name=collection_name,
            aclient=self._client,
            enable_hybrid=is_hybrid,
            fastembed_sparse_model="Qdrant/bm25" if is_hybrid else None,
        )
        index = VectorStoreIndex.from_vector_store(store)
        self._indices[collection_name] = index
        return index

    async def _retrieve_from_collection(self, prompt: str, col_name: str) -> List[str]:
        """Retrieves top candidates from a single collection via hybrid or dense search."""
        try:
            index = await self._get_or_create_index(col_name)
            is_hybrid = self._hybrid_support.get(col_name, False)
            if is_hybrid:
                retriever = index.as_retriever(
                    vector_store_query_mode="hybrid",
                    similarity_top_k=TOP_K_RETRIEVE,
                    alpha=0.5,
                )
            else:
                retriever = index.as_retriever(similarity_top_k=TOP_K_RETRIEVE)

            nodes = await retriever.aretrieve(prompt)
            return [n.get_content() for n in nodes]
        except Exception as e:
            print(f"[Cortex] Error retrieving from collection '{col_name}': {e}")
            return []

    async def query(self, prompt: str, collection: Optional[str | List[str]] = None) -> List[str]:
        """
        Federated hybrid search across one or more collections, followed by
        unified cross-encoder reranking.
        Supports:
          - collection="all" (queries all available Qdrant collections)
          - collection="col1,col2" (queries comma-separated collections)
          - collection=["col1", "col2"]
          - collection=None (defaults to QDRANT_COLLECTION in .env)
        """
        if self._sync_client is None:
            raise RuntimeError("RetrievalEngine not initialized. Call await engine.initialize() first.")

        # 1. Resolve target collections
        target_cols: List[str] = []
        if collection == "all":
            try:
                cols = self._sync_client.get_collections().collections
                target_cols = [c.name for c in cols]
            except Exception:
                target_cols = [DEFAULT_COLLECTION]
        elif isinstance(collection, str) and "," in collection:
            target_cols = [c.strip() for c in collection.split(",") if c.strip()]
        elif isinstance(collection, list):
            target_cols = [c.strip() for c in collection if c.strip()]
        elif collection:
            target_cols = [collection]
        else:
            target_cols = [DEFAULT_COLLECTION]

        if not target_cols:
            target_cols = [DEFAULT_COLLECTION]

        # 2. Parallel hybrid retrieval across all target collections
        import asyncio
        batch_results = await asyncio.gather(*[
            self._retrieve_from_collection(prompt, col) for col in target_cols
        ])

        # 3. Pool candidates with collection provenance if multiple collections
        pooled_candidates: List[str] = []
        for col_name, candidates in zip(target_cols, batch_results):
            for cand in candidates:
                if len(target_cols) > 1 and not cand.startswith(f"# Collection: {col_name}"):
                    pooled_candidates.append(f"# Collection: {col_name}\n{cand}")
                else:
                    pooled_candidates.append(cand)

        if not pooled_candidates:
            return []

        # 4. Joint Stage-2 cross-encoder reranking
        return await self._rerank(prompt, pooled_candidates)

    async def _rerank(self, query: str, passages: List[str]) -> List[str]:
        """POST to oMLX /v1/rerank and return sorted passages."""
        # Truncate overly long documents to 1200 chars for fast cross-encoder evaluation
        eval_docs = [p[:1200] for p in passages]
        payload = {
            "model": os.getenv("RERANK_MODEL_NAME", "vserifsaglam--Qwen3-Reranker-4B-4bit-MLX"),
            "query": query,
            "documents": eval_docs,
        }
        rerank_key = os.getenv("RERANK_API_KEY") or os.getenv("COHERE_API_KEY")
        headers = {"Authorization": f"Bearer {rerank_key}"} if rerank_key else {}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{RERANK_BASE_URL}/rerank", json=payload, headers=headers)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            sorted_results = sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True)
            return [passages[r["index"]] for r in sorted_results[:TOP_K_RERANK]]
        except Exception as e:
            # Degrade gracefully: return first TOP_K_RERANK without reranking
            err_msg = f"{type(e).__name__}: {e or 'timeout'}"
            print(f"[Cortex] Reranker unavailable ({err_msg}), returning top-k by vector score.")
            return passages[:TOP_K_RERANK]
