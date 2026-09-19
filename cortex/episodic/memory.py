"""
Temporal knowledge graph memory using Graphiti + FalkorDB.

Facts are stored as graph edges with temporal validity windows.
When new contradictory information arrives, Graphiti marks old facts as
superseded (soft-delete) rather than creating conflicting vectors.
All LLM calls are routed through the local proxy at :8003.
"""
import os
from datetime import datetime, timezone
from typing import List

import httpx
from dotenv import load_dotenv
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.nodes import EpisodeType
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

load_dotenv()
os.environ.setdefault("OPENAI_API_KEY", "not-needed")

FALKORDB_HOST   = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT   = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASS   = os.getenv("FALKORDB_PASSWORD", "")
LLM_PROXY_URL   = os.getenv("LLM_PROXY_URL", "http://localhost:8003/v1")
EMBED_BASE_URL  = os.getenv("EMBED_BASE_URL", "http://localhost:8000/v1")
RERANK_BASE_URL = os.getenv("RERANK_BASE_URL", "http://localhost:8000/v1")


class LocalRerankerClient(CrossEncoderClient):
    """Reranks candidates via local oMLX (:8000/v1/rerank) or falls back safely."""

    def __init__(self, base_url: str = RERANK_BASE_URL, model_name: str | None = None):
        self.base_url = base_url
        self.model_name = model_name or os.getenv("RERANK_MODEL_NAME", "vserifsaglam--Qwen3-Reranker-4B-4bit-MLX")

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": passages,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{self.base_url}/rerank", json=payload)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            ranked = [(passages[r["index"]], float(r.get("relevance_score", 0.0))) for r in results]
            return sorted(ranked, key=lambda x: x[1], reverse=True)
        except Exception:
            return [(passage, 1.0) for passage in passages]


class EpisodicMemory:
    """Wraps Graphiti for temporal fact storage and retrieval."""

    def __init__(self):
        self._graphiti: Graphiti | None = None

    async def initialize(self):
        llm_client = OpenAIGenericClient(
            config=LLMConfig(
                api_key="not-needed",
                model="cortex-proxy",   # model name forwarded to proxy (overridden there)
                base_url=LLM_PROXY_URL,
            )
        )
        embedder = OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key="not-needed",
                embedding_model=os.getenv("EMBED_MODEL_NAME", "mlx-community--Qwen3-Embedding-8B-4bit-DWQ"),
                base_url=EMBED_BASE_URL,
            )
        )
        cross_encoder = LocalRerankerClient(
            base_url=RERANK_BASE_URL,
            model_name=os.getenv("RERANK_MODEL_NAME", "vserifsaglam--Qwen3-Reranker-4B-4bit-MLX"),
        )
        driver = FalkorDriver(
            host=FALKORDB_HOST,
            port=FALKORDB_PORT,
            password=FALKORDB_PASS or None,
            database=os.getenv("FALKORDB_DATABASE", "cortex"),
        )
        self._graphiti = Graphiti(
            graph_driver=driver,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )
        await self._graphiti.build_indices_and_constraints()

    async def search(self, query: str, limit: int = 5) -> List[str]:
        """Retrieve temporally-valid facts matching the query."""
        if self._graphiti is None:
            return []
        results = await self._graphiti.search(query, num_results=limit)
        return [r.fact for r in results]

    async def record_outcome(
        self,
        episode_name: str,
        content: str,
        tags: list[str] | None = None,
    ) -> None:
        """Persist a new episode to the temporal KG."""
        if self._graphiti is None:
            raise RuntimeError("EpisodicMemory not initialized.")
        tagged_content = content
        if tags:
            tagged_content += f"\n\nTags: {', '.join(tags)}"
        await self._graphiti.add_episode(
            name=episode_name,
            episode_body=tagged_content,
            source=EpisodeType.text,
            source_description="cortex_outcome_recorder",
            reference_time=datetime.now(timezone.utc),
        )

    async def delete_fact(self, fact_id: str, reason: str) -> None:
        """Mark a specific fact as superseded (soft-delete)."""
        if self._graphiti is None:
            raise RuntimeError("EpisodicMemory not initialized.")
        # Graphiti invalidate API: marks the episode invalid with a reason note
        await self._graphiti.add_episode(
            name=f"SUPERSEDE:{fact_id}",
            episode_body=f"This fact has been superseded. Reason: {reason}",
            source=EpisodeType.text,
            source_description="cortex_supersede",
            reference_time=datetime.now(timezone.utc),
        )

    async def close(self):
        if self._graphiti:
            await self._graphiti.close()
