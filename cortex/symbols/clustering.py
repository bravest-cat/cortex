"""
Subsystem Community Detection via Louvain Modularity on FalkorDB Dependency Graphs.

Clusters collaborating files into high-cohesion architectural subsystems,
generating executive architectural descriptions and persisting (:Subsystem) nodes
and [:BELONGS_TO] edges directly into FalkorDB.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import networkx as nx
from networkx.algorithms.community import louvain_communities
import redis

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASS = os.getenv("FALKORDB_PASSWORD", "")


class SubsystemClusterer:
    """Detects and persists architectural subsystems using Louvain community detection."""

    def __init__(
        self,
        host: str = FALKORDB_HOST,
        port: int = FALKORDB_PORT,
        password: Optional[str] = FALKORDB_PASS,
        graph_name: str = "cortex_symbols",
    ):
        self.host = host
        self.port = port
        self.password = password or None
        self.graph_name = graph_name
        self._r: Optional[redis.Redis] = None

    def _get_client(self) -> redis.Redis:
        if self._r is None:
            self._r = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                decode_responses=True,
            )
        return self._r

    def _query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
        client = self._get_client()
        try:
            formatted = cypher
            if params:
                for k, v in params.items():
                    if isinstance(v, str):
                        escaped_v = v.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
                        formatted = formatted.replace(f"${k}", f"'{escaped_v}'")
                    elif v is None:
                        formatted = formatted.replace(f"${k}", "null")
                    elif isinstance(v, (int, float)):
                        formatted = formatted.replace(f"${k}", str(v))
            res = client.execute_command("GRAPH.QUERY", self.graph_name, formatted)
            return res if res and isinstance(res, list) else []
        except Exception as e:
            print(f"[SubsystemClusterer] Cypher error: {e}")
            return []

    def _generate_subsystem_summary(self, name: str, files: List[str], custom_summary: Optional[str] = None) -> str:
        """
        Synthesizes a crisp, factual architectural description deterministically
        from member files and primary exported symbols without local LLM overhead.
        """
        if custom_summary:
            return custom_summary

        file_basenames = [Path(f).name for f in files]
        top_symbols = []
        try:
            for f in files[:4]:
                rows = self._query(
                    "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File {path: $f}) RETURN s.name LIMIT 3",
                    {"f": f}
                )
                if rows and len(rows) > 1 and isinstance(rows[1], list):
                    for r in rows[1]:
                        if r and len(r) >= 1:
                            top_symbols.append(str(r[0]))
        except Exception:
            pass

        files_str = ", ".join(file_basenames[:4])
        if len(file_basenames) > 4:
            files_str += f" (+{len(file_basenames) - 4} more)"

        if top_symbols:
            unique_syms = list(dict.fromkeys(top_symbols))[:5]
            syms_str = ", ".join(unique_syms)
            return f"Architectural subsystem '{name}' comprising {len(files)} components ({files_str}) exposing primary symbols: {syms_str}."
        return f"Architectural subsystem '{name}' encapsulating {len(files)} components ({files_str})."

    async def detect_subsystems(self, persist: bool = True) -> List[Dict[str, Any]]:
        """
        Runs Louvain community detection on file dependencies and optionally persists
        (:Subsystem) nodes and [:BELONGS_TO] edges into FalkorDB.
        """
        # 1. Fetch file dependency matrix
        cypher_edges = (
            "MATCH (f1:File)-[r:IMPORTS|REFERENCES]->(s:Symbol)-[:DEFINED_IN]->(f2:File) "
            "WHERE f1.path <> f2.path "
            "RETURN f1.path, f2.path, count(r)"
        )
        edge_rows = self._query(cypher_edges)

        G = nx.Graph()

        # Add all files (even if disconnected)
        file_rows = self._query("MATCH (f:File) RETURN f.path")
        if file_rows and len(file_rows) > 1 and isinstance(file_rows[1], list):
            for row in file_rows[1]:
                if row:
                    G.add_node(str(row[0]))

        if edge_rows and len(edge_rows) > 1 and isinstance(edge_rows[1], list):
            for row in edge_rows[1]:
                if len(row) >= 3:
                    u, v, w = str(row[0]), str(row[1]), int(row[2])
                    if G.has_edge(u, v):
                        G[u][v]["weight"] += w
                    else:
                        G.add_edge(u, v, weight=w)

        if len(G.nodes) == 0:
            return []

        # 2. Compute Louvain communities
        communities = louvain_communities(G, weight="weight", seed=42)

        # 3. Label and synthesize descriptions
        now = datetime.now(timezone.utc).isoformat()
        subsystems = []

        # Clear existing subsystem nodes first if persisting
        if persist:
            self._query("MATCH (sub:Subsystem) DETACH DELETE sub")

        for idx, comm in enumerate(communities):
            file_list = sorted(list(comm))
            # Derive sensible subsystem name from common directory or file names
            paths = [Path(p) for p in file_list]
            if len(paths) == 1:
                sub_name = paths[0].stem.capitalize()
            else:
                # Find common parent or directory name
                parts = [p.parts for p in paths]
                common_parts = []
                for p_idx in range(min(len(p) for p in parts)):
                    if len(set(p[p_idx] for p in parts)) == 1:
                        common_parts.append(parts[0][p_idx])
                    else:
                        break
                if common_parts and len(common_parts) > 1:
                    sub_name = common_parts[-1].capitalize()
                else:
                    sub_name = f"Module_{idx+1}"

            summary = self._generate_subsystem_summary(sub_name, file_list)

            subsystems.append({
                "id": f"subsystem_{idx+1}",
                "name": sub_name,
                "description": summary,
                "file_count": len(file_list),
                "files": file_list,
            })

            if persist:
                self._query(
                    "MERGE (sub:Subsystem {id: $id, name: $name}) "
                    "SET sub.description = $desc, sub.file_count = $count, sub.updated_at = $now",
                    {
                        "id": f"subsystem_{idx+1}",
                        "name": sub_name,
                        "desc": summary,
                        "count": len(file_list),
                        "now": now,
                    },
                )
                for fp in file_list:
                    self._query(
                        "MATCH (sub:Subsystem {id: $id}), (f:File {path: $fp}) "
                        "MERGE (f)-[:BELONGS_TO]->(sub)",
                        {"id": f"subsystem_{idx+1}", "fp": fp},
                    )

        return subsystems
