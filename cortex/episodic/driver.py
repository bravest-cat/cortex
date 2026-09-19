"""
Agent-Native Episodic Memory Driver for FalkorDB.

Eliminates the :8003 LLM proxy daemon completely.
The main Antigravity agent supplies structured entities, facts, and supersedes directly,
or passes a narrative which is embedded via local oMLX :8000 (Metal GPU) and committed
directly to FalkorDB via native GraphBLAS commands.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
import redis

from cortex.falkordb_client import FalkorDBClient, FALKORDB_HOST, FALKORDB_PORT, FALKORDB_PASS

FALKORDB_DB   = os.getenv("FALKORDB_DATABASE", "cortex")

EMBED_BASE_URL  = os.getenv("EMBED_BASE_URL", "http://localhost:8000/v1")
EMBED_MODEL     = os.getenv("EMBED_MODEL_NAME", "mlx-community--Qwen3-Embedding-8B-4bit-DWQ")
LLM_BASE_URL    = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_MODEL       = os.getenv("LLM_MODEL_NAME", "Qwen-3.5")


class AgentNativeEpisodicDriver:
    """Direct FalkorDB driver for Episodic & Temporal Knowledge Memory."""

    def __init__(
        self,
        host: str = FALKORDB_HOST,
        port: int = FALKORDB_PORT,
        password: Optional[str] = FALKORDB_PASS,
        db_name: str = FALKORDB_DB,
    ):
        self.host = host
        self.port = port
        self.password = password or None
        self.db_name = db_name
        self.client = FalkorDBClient(host=self.host, port=self.port, password=self.password)

    def _get_client(self) -> redis.Redis:
        return self.client.get_redis()

    def _query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
        return self.client.query(self.db_name, cypher, params)

    async def get_embedding(self, text: str) -> List[float]:
        """Generates embedding via local oMLX :8000 Metal GPU."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{EMBED_BASE_URL}/embeddings",
                    json={"model": EMBED_MODEL, "input": text[:2000]},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["data"][0]["embedding"]
        except Exception as e:
            print(f"[AgentNativeMemory] Warning generating embedding: {e}")
        return []

    async def extract_facts_from_text(self, text: str) -> Dict[str, Any]:
        """Fallback: uses local Qwen-3.5 on :8000 directly if no structured facts passed."""
        system_prompt = (
            "You are an expert knowledge graph extractor. Extract 2-4 core entities and relationships "
            "from the technical narrative. Output JSON only matching:\n"
            '{"entities": [{"name": "...", "type": "...", "summary": "..."}], '
            '"facts": [{"source": "...", "relation": "...", "target": "...", "fact": "..."}]}'
        )
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{LLM_BASE_URL}/chat/completions",
                    json={
                        "model": LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": text[:1500]},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 400,
                    },
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    # parse json
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    return json.loads(content)
        except Exception as e:
            print(f"[AgentNativeMemory] Direct LLM extraction fallback warning: {e}")
        return {"entities": [], "facts": []}

    async def record_outcome(
        self,
        episode_name: str,
        content: str,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        entities: Optional[List[Dict[str, str]]] = None,
        facts: Optional[List[Dict[str, str]]] = None,
        supersedes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Agent-Native recording: Inserts (:Episodic), (:Entity), and [:RELATES_TO] edges
        directly into FalkorDB in Graphiti's schema with zero proxy daemon.
        """
        now = datetime.now(timezone.utc).isoformat()
        proj = project or "cortex"

        # 1. Supersede older conflicting facts if specified
        superseded_count = 0
        if supersedes:
            for pattern in supersedes:
                res = self._query(
                    "MATCH (old:Episodic) WHERE (old.name CONTAINS $pat OR old.content CONTAINS $pat) "
                    "AND (old.superseded IS NULL OR old.superseded = false) "
                    "SET old.superseded = true, old.superseded_by = $by, old.superseded_at = $now, "
                    "old.superseded_reason = 'Superseded by ' + $by "
                    "RETURN count(old)",
                    {"pat": pattern, "by": episode_name, "now": now},
                )
                if res and len(res) > 1 and res[1]:
                    try:
                        superseded_count += int(res[1][0][0])
                    except Exception:
                        pass

        # 2. Persist (:Episodic) Node
        tags_str = ", ".join(tags) if tags else ""
        self._query(
            "MERGE (e:Episodic {name: $name}) "
            "SET e.content = $content, e.created_at = $now, e.project = $project, "
            "e.tags = $tags, e.superseded = false",
            {
                "name": episode_name,
                "content": content,
                "now": now,
                "project": proj,
                "tags": tags_str,
            },
        )

        # 3. Handle Entities & Facts
        actual_entities = entities or []
        actual_facts = facts or []

        # If agent didn't provide structured entities/facts, use fast local Qwen-3.5 on :8000 directly
        if not actual_entities and not actual_facts:
            extracted = await self.extract_facts_from_text(content)
            actual_entities = extracted.get("entities", [])
            actual_facts = extracted.get("facts", [])

        # Write Entities
        for ent in actual_entities:
            ename = ent.get("name", "").strip()
            if not ename:
                continue
            esummary = ent.get("summary") or ename
            etype = ent.get("type", "concept")

            self._query(
                "MERGE (n:Entity {name: $name}) "
                "SET n.summary = $summary, n.entity_type = $type, n.updated_at = $now "
                "WITH n "
                "MATCH (e:Episodic {name: $ename}) "
                "MERGE (e)-[:MENTIONS]->(n)",
                {
                    "name": ename,
                    "summary": esummary,
                    "type": etype,
                    "now": now,
                    "ename": episode_name,
                },
            )

        # Write Fact Edges
        for f in actual_facts:
            src = f.get("source", "").strip()
            tgt = f.get("target", "").strip()
            rel = (f.get("relation") or "RELATES_TO").upper().replace(" ", "_")
            fact_text = f.get("fact") or f"{src} {rel} {tgt}"

            if src and tgt:
                self._query(
                    "MERGE (s:Entity {name: $src}) "
                    "MERGE (t:Entity {name: $tgt}) "
                    "MERGE (s)-[r:RELATES_TO {fact: $fact, relation: $rel}]->(t) "
                    "SET r.created_at = $now, r.superseded = false",
                    {
                        "src": src,
                        "tgt": tgt,
                        "fact": fact_text,
                        "rel": rel,
                        "now": now,
                    },
                )

        return {
            "status": "ok",
            "episode": episode_name,
            "entities_written": len(actual_entities),
            "facts_written": len(actual_facts),
            "superseded_count": superseded_count,
        }

    async def search(self, query: str, limit: int = 5) -> List[str]:
        """
        Retrieves temporally valid, non-superseded facts from FalkorDB.
        Filters out superseded historical facts.
        """
        q_tokens = [w.lower() for w in query.split() if len(w) > 2]
        if not q_tokens:
            return []

        # 1. Fetch facts matching query terms where NOT superseded
        results: List[str] = []
        cypher = (
            "MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) "
            "WHERE (r.superseded IS NULL OR r.superseded = false) "
            "RETURN coalesce(r.fact, s.name + ' ' + r.relation + ' ' + t.name), s.name, t.name "
            "LIMIT 50"
        )
        rows = self._query(cypher)
        if rows and len(rows) > 1 and isinstance(rows[1], list):
            scored = []
            for row in rows[1]:
                if row:
                    fact_str = str(row[0])
                    f_lower = fact_str.lower()
                    score = sum(3 for tok in q_tokens if tok in f_lower)
                    if score > 0:
                        scored.append((score, fact_str))
            scored.sort(key=lambda x: x[0], reverse=True)
            results.extend([f for _, f in scored[:limit]])

        # 2. Also retrieve matching non-superseded episodic descriptions
        if len(results) < limit:
            cypher_ep = (
                "MATCH (e:Episodic) "
                "WHERE (e.superseded IS NULL OR e.superseded = false) "
                "RETURN e.name, e.content LIMIT 30"
            )
            ep_rows = self._query(cypher_ep)
            if ep_rows and len(ep_rows) > 1 and isinstance(ep_rows[1], list):
                ep_scored = []
                for row in ep_rows[1]:
                    if len(row) >= 2:
                        name, content = str(row[0]), str(row[1])
                        text_lower = (name + " " + content).lower()
                        score = sum(2 for tok in q_tokens if tok in text_lower)
                        if score > 0:
                            summary = f"[{name}]: {content[:200]}"
                            ep_scored.append((score, summary))
                ep_scored.sort(key=lambda x: x[0], reverse=True)
                for _, s in ep_scored:
                    if s not in results and len(results) < limit:
                        results.append(s)

        return results

    async def delete_fact(self, fact_id: str, reason: str) -> None:
        """Marks an episodic node or fact relation as superseded."""
        now = datetime.now(timezone.utc).isoformat()
        self._query(
            "MATCH (e:Episodic) WHERE e.name = $id OR e.name CONTAINS $id "
            "SET e.superseded = true, e.superseded_reason = $reason, e.superseded_at = $now",
            {"id": fact_id, "reason": reason, "now": now},
        )
        self._query(
            "MATCH ()-[r:RELATES_TO]->() WHERE r.fact CONTAINS $id "
            "SET r.superseded = true, r.superseded_reason = $reason, r.superseded_at = $now",
            {"id": fact_id, "reason": reason, "now": now},
        )

    async def initialize(self) -> None:
        """Verify connection to FalkorDB."""
        client = self._get_client()
        client.ping()

    async def close(self) -> None:
        """Close FalkorDB connection."""
        if self._r is not None:
            try:
                self._r.close()
            except Exception:
                pass
            self._r = None

