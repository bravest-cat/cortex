"""
Canonical FalkorDB Client & Token-Safe Cypher Query Engine for Cortex.

Provides:
1. Resilient Redis connection lifecycle and reconnect logic.
2. Token-safe Cypher parameter interpolation using word boundaries
   and length-descending key sorting (eliminating prefix collision vulnerabilities).
3. Schema index verification for Symbol, File, Document, Tag, and Concept nodes.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import redis

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASS = os.getenv("FALKORDB_PASSWORD", "")


def format_cypher_query(cypher: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Interpolates parameters into a Cypher query string with token-safe word boundary regex
    and length-descending key sorting to prevent substring corruption (e.g. $s before $s_target).
    """
    if not params:
        return cypher

    formatted = cypher
    for k in sorted(params.keys(), key=len, reverse=True):
        v = params[k]
        if isinstance(v, str):
            escaped_v = v.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
            val_str = f"'{escaped_v}'"
        elif v is None:
            val_str = "null"
        elif isinstance(v, bool):
            val_str = "true" if v else "false"
        elif isinstance(v, (int, float)):
            val_str = str(v)
        else:
            val_str = f"'{str(v)}'"

        pattern = r'\$' + re.escape(k) + r'\b'
        formatted = re.sub(pattern, lambda _: val_str, formatted)

    return formatted


class FalkorDBClient:
    """Canonical FalkorDB Redis client with robust query execution and indexing."""

    def __init__(
        self,
        host: str = FALKORDB_HOST,
        port: int = FALKORDB_PORT,
        password: Optional[str] = FALKORDB_PASS,
    ):
        self.host = host
        self.port = port
        self.password = password or None
        self._r: Optional[redis.Redis] = None
        self._verified_indexes: Set[Tuple[str, str, str]] = set()

    def get_redis(self) -> redis.Redis:
        """Returns active Redis client connection."""
        if self._r is None:
            self._r = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                decode_responses=True,
            )
        return self._r

    def ensure_indexes(self, graph_name: str, indexes: List[Tuple[str, str]]):
        """
        Ensures B-tree schema indexes on specified labels and properties.
        indexes: list of (node_label, property_name)
        """
        client = self.get_redis()
        for label, prop in indexes:
            key = (graph_name, label, prop)
            if key in self._verified_indexes:
                continue
            try:
                client.execute_command("GRAPH.QUERY", graph_name, f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
                self._verified_indexes.add(key)
            except Exception:
                self._verified_indexes.add(key)

    def query(
        self,
        graph_name: str,
        cypher: str,
        params: Optional[Dict[str, Any]] = None,
        attempts: int = 2,
    ) -> List[List[Any]]:
        """
        Executes a Cypher query on the specified graph with token-safe parameter binding and reconnect.
        """
        formatted_cypher = format_cypher_query(cypher, params)
        client = self.get_redis()

        for attempt in range(attempts):
            try:
                res = client.execute_command("GRAPH.QUERY", graph_name, formatted_cypher)
                return res if res and isinstance(res, list) else []
            except (redis.ConnectionError, redis.TimeoutError):
                self._r = None
                client = self.get_redis()
                if attempt == attempts - 1:
                    raise
            except Exception as e:
                print(f"[FalkorDBClient] Error executing query on '{graph_name}': {e}")
                return []
        return []


_SINGLETON_CLIENT: Optional[FalkorDBClient] = None


def get_shared_falkordb_client(
    host: str = FALKORDB_HOST,
    port: int = FALKORDB_PORT,
    password: Optional[str] = FALKORDB_PASS,
) -> FalkorDBClient:
    """Returns singleton FalkorDBClient instance."""
    global _SINGLETON_CLIENT
    if _SINGLETON_CLIENT is None:
        _SINGLETON_CLIENT = FalkorDBClient(host=host, port=port, password=password)
    return _SINGLETON_CLIENT
