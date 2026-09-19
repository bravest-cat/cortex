"""
SCIP (Source Code Intelligence Protocol) Graph Ingestion Engine.

Ingests compiler-grade, type-checked index.scip Protobuf files into FalkorDB
(cortex_symbols), populating:
  (:Symbol {name, scip_id, kind})
  (:File {path})
  (s:Symbol)-[:DEFINED_IN {kind, line, signature, docstring, parent, scip_id}]->(f:File)
  (f:File)-[:IMPORTS {line, module, scip_id}]->(s:Symbol)
  (f:File)-[:REFERENCES {line, kind, role, scip_id}]->(s:Symbol)
  (s1:Symbol)-[:IMPLEMENTS]->(s2:Symbol)

Complements Tree-sitter AST parsing with exact compiler-grade cross-file type resolution.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import redis

from cortex.symbols import scip_pb2
from cortex.falkordb_client import FalkorDBClient, FALKORDB_HOST, FALKORDB_PORT, FALKORDB_PASS

SYMBOLS_GRAPH_NAME = os.getenv("FALKORDB_SYMBOLS_GRAPH", "cortex_symbols")


def parse_scip_symbol_name(scip_sym: str) -> str:
    """
    Extracts a human-readable symbol name from a SCIP symbol string.
    Examples:
      'scip-python python cortex 0.1.0 `cortex.server`/trigger_dream_mode().' -> 'trigger_dream_mode'
      'rust-analyzer cargo cortex 0.1.0 Engine#execute().' -> 'execute'
      'rust-analyzer cargo cortex 0.1.0 Engine#' -> 'Engine'
      'local 42' -> 'local:42'
    """
    if not scip_sym:
        return "anonymous"
    if scip_sym.startswith("local "):
        return scip_sym.replace(" ", ":")
    clean = scip_sym.rstrip("/#().:$!")
    tokens = re.split(r"[\s/#/`]+", clean)
    name = tokens[-1] if tokens else scip_sym
    return name.strip("`().#") or scip_sym


def extract_parent_name(scip_sym: str, enclosing_sym: Optional[str] = None) -> str:
    """Extracts the parent class/struct/namespace name if present."""
    if enclosing_sym:
        return parse_scip_symbol_name(enclosing_sym)
    if "#" in scip_sym:
        parts = scip_sym.split("#")
        if len(parts) > 1 and parts[0]:
            clean_parent = parts[0].rstrip("/#().:$!")
            tokens = re.split(r"[\s/#/`]+", clean_parent)
            return tokens[-1].strip("`().#") if tokens else ""
    return ""


def scip_kind_to_str(kind_int: int) -> str:
    """Converts a SCIP SymbolInformation.Kind enum int to a lowercase string."""
    try:
        name = scip_pb2.SymbolInformation.Kind.Name(kind_int)
        if name == "UnspecifiedKind":
            return "symbol"
        return name.lower()
    except Exception:
        return "symbol"


class SCIPImporter:
    """Parses and commits SCIP index files to the FalkorDB symbol graph."""

    def __init__(
        self,
        host: str = FALKORDB_HOST,
        port: int = FALKORDB_PORT,
        password: Optional[str] = FALKORDB_PASS,
        graph_name: str = SYMBOLS_GRAPH_NAME,
    ):
        self.host = host
        self.port = port
        self.password = password or None
        self.graph_name = graph_name
        self.client = FalkorDBClient(host=self.host, port=self.port, password=self.password)

    def _get_client(self) -> redis.Redis:
        return self.client.get_redis()

    def _ensure_indexes(self):
        """Ensures B-tree schema indexes on :Symbol(name) and :File(path)."""
        self.client.ensure_indexes(
            self.graph_name,
            [
                ("Symbol", "name"),
                ("File", "path"),
            ],
        )

    def _query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
        """Executes a Cypher query on the SCIP symbols graph with token-safe parameter binding."""
        self._ensure_indexes()
        return self.client.query(self.graph_name, cypher, params)

    def load_index(self, source: Union[str, Path, bytes]) -> scip_pb2.Index:
        """Loads and deserializes a SCIP Protobuf Index."""
        index = scip_pb2.Index()
        if isinstance(source, (str, Path)):
            with open(source, "rb") as f:
                index.ParseFromString(f.read())
        elif isinstance(source, bytes):
            index.ParseFromString(source)
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")
        return index

    def ingest_index(
        self,
        source: Union[str, Path, bytes, scip_pb2.Index],
        purge_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        Ingests a SCIP index into FalkorDB.
        Creates File nodes, Symbol nodes, DEFINED_IN, IMPORTS, REFERENCES, and IMPLEMENTS edges.
        """
        start_time = time.perf_counter()

        if isinstance(source, scip_pb2.Index):
            index = source
        else:
            index = self.load_index(source)

        doc_count = len(index.documents)
        total_symbols = 0
        total_references = 0
        total_relationships = 0

        # Ingest external symbols if present
        for ext_sym in index.external_symbols:
            name = ext_sym.display_name or parse_scip_symbol_name(ext_sym.symbol)
            kind = scip_kind_to_str(ext_sym.kind)
            self._query(
                "MERGE (s:Symbol {name: $name}) "
                "SET s.scip_id = $scip_id, s.kind = $kind",
                {"name": name, "scip_id": ext_sym.symbol, "kind": kind},
            )

        for doc in index.documents:
            file_path = doc.relative_path
            if not file_path:
                continue

            # 1. Clean previous links for this file if requested
            if purge_existing:
                self._query(
                    "MATCH (f:File {path: $path}) "
                    "OPTIONAL MATCH (f)<-[r1:DEFINED_IN]-() DELETE r1 "
                    "WITH f "
                    "OPTIONAL MATCH (f)-[r2:IMPORTS]->() DELETE r2 "
                    "WITH f "
                    "OPTIONAL MATCH (f)-[r3:REFERENCES]->() DELETE r3",
                    {"path": file_path},
                )

            # Ensure File node exists
            self._query("MERGE (f:File {path: $path})", {"path": file_path})

            # Map symbols declared in doc.symbols
            symbol_info_map: Dict[str, Any] = {}
            for sym_info in doc.symbols:
                s_id = sym_info.symbol
                name = sym_info.display_name or parse_scip_symbol_name(s_id)
                kind = scip_kind_to_str(sym_info.kind)
                docstring = "\n".join(sym_info.documentation)[:500]
                parent = extract_parent_name(s_id, sym_info.enclosing_symbol)
                symbol_info_map[s_id] = {
                    "name": name,
                    "kind": kind,
                    "docstring": docstring,
                    "parent": parent,
                    "relationships": sym_info.relationships,
                }

            # Map occurrences to declarations, imports, and references
            seen_refs = set()
            for occ in doc.occurrences:
                s_id = occ.symbol
                if not s_id:
                    continue

                roles = occ.symbol_roles
                start_line = occ.range[0] + 1 if occ.range else 1

                info = symbol_info_map.get(s_id, {})
                name = info.get("name") or parse_scip_symbol_name(s_id)
                kind = info.get("kind") or "symbol"
                docstring = info.get("docstring") or ""
                parent = info.get("parent") or extract_parent_name(s_id)

                is_def = bool(roles & scip_pb2.SymbolRole.Definition)
                is_imp = bool(roles & scip_pb2.SymbolRole.Import)

                # Ensure Symbol node exists
                self._query(
                    "MERGE (s:Symbol {name: $name}) "
                    "SET s.scip_id = $scip_id, s.kind = $kind",
                    {"name": name, "scip_id": s_id, "kind": kind},
                )

                if is_def:
                    self._query(
                        "MATCH (s:Symbol {name: $name}) "
                        "MATCH (f:File {path: $path}) "
                        "MERGE (s)-[:DEFINED_IN {kind: $kind, line: $line, signature: '', docstring: $doc, parent: $parent, scip_id: $scip_id}]->(f)",
                        {
                            "name": name,
                            "path": file_path,
                            "kind": kind,
                            "line": start_line,
                            "doc": docstring,
                            "parent": parent,
                            "scip_id": s_id,
                        },
                    )
                    total_symbols += 1

                elif is_imp:
                    self._query(
                        "MATCH (s:Symbol {name: $name}) "
                        "MATCH (f:File {path: $path}) "
                        "MERGE (f)-[:IMPORTS {module: '', line: $line, scip_id: $scip_id}]->(s)",
                        {
                            "name": name,
                            "path": file_path,
                            "line": start_line,
                            "scip_id": s_id,
                        },
                    )

                else:
                    # Regular reference / call / read-write access
                    ref_key = (name, start_line)
                    if ref_key not in seen_refs:
                        seen_refs.add(ref_key)
                        ref_kind = "write" if (roles & scip_pb2.SymbolRole.WriteAccess) else "call"
                        self._query(
                            "MATCH (s:Symbol {name: $name}) "
                            "MATCH (f:File {path: $path}) "
                            "MERGE (f)-[:REFERENCES {line: $line, kind: $kind, scip_id: $scip_id}]->(s)",
                            {
                                "name": name,
                                "path": file_path,
                                "line": start_line,
                                "kind": ref_kind,
                                "scip_id": s_id,
                            },
                        )
                        total_references += 1

            # Ingest symbol relationships (e.g. IMPLEMENTS)
            for s_id, info in symbol_info_map.items():
                s_name = info["name"]
                for rel in info.get("relationships", []):
                    target_id = rel.symbol
                    target_name = parse_scip_symbol_name(target_id)
                    if rel.is_implementation:
                        self._query(
                            "MERGE (s1:Symbol {name: $s1}) "
                            "MERGE (s2:Symbol {name: $s2}) "
                            "MERGE (s1)-[:IMPLEMENTS {scip_id: $scip_id}]->(s2)",
                            {"s1": s_name, "s2": target_name, "scip_id": target_id},
                        )
                        total_relationships += 1
                    elif rel.is_type_definition:
                        self._query(
                            "MERGE (s1:Symbol {name: $s1}) "
                            "MERGE (s2:Symbol {name: $s2}) "
                            "MERGE (s1)-[:TYPE_OF {scip_id: $scip_id}]->(s2)",
                            {"s1": s_name, "s2": target_name, "scip_id": target_id},
                        )
                        total_relationships += 1

        duration = time.perf_counter() - start_time
        return {
            "status": "ok",
            "documents_indexed": doc_count,
            "symbols_indexed": total_symbols,
            "references_indexed": total_references,
            "relationships_indexed": total_relationships,
            "duration_seconds": round(duration, 4),
            "graph_name": self.graph_name,
        }


def detect_project_scip_indexer(workspace_root: str) -> Dict[str, Any]:
    """
    Inspects project manifests to determine appropriate SCIP generator and installation instructions.
    """
    root = Path(workspace_root).resolve()

    # 1. Rust
    if (root / "Cargo.toml").exists():
        return {
            "language": "rust",
            "manifest": "Cargo.toml",
            "cmd": ["rust-analyzer", "scip", "."],
            "binary": "rust-analyzer",
            "install_hint": "brew install rust-analyzer  OR  rustup component add rust-analyzer",
            "output_file": "index.scip",
        }

    # 2. TypeScript / JavaScript
    if (root / "package.json").exists() or (root / "tsconfig.json").exists():
        bin_name = "scip-typescript"
        cmd = [bin_name, "index"]
        if not shutil.which(bin_name) and shutil.which("npx"):
            bin_name = "npx"
            cmd = ["npx", "@sourcegraph/scip-typescript", "index"]
        return {
            "language": "typescript",
            "manifest": "package.json",
            "cmd": cmd,
            "binary": bin_name,
            "install_hint": "npm install -g @sourcegraph/scip-typescript",
            "output_file": "index.scip",
        }

    # 3. Python
    if any((root / f).exists() for f in ("pyproject.toml", "setup.py", "requirements.txt")):
        return {
            "language": "python",
            "manifest": "pyproject.toml / requirements.txt",
            "cmd": ["scip-python", "index", "."],
            "binary": "scip-python",
            "install_hint": "pip install scip-python",
            "output_file": "index.scip",
        }

    # 4. Go
    if (root / "go.mod").exists():
        return {
            "language": "go",
            "manifest": "go.mod",
            "cmd": ["scip-go"],
            "binary": "scip-go",
            "install_hint": "go install github.com/sourcegraph/scip-go/cmd/scip-go@latest",
            "output_file": "index.scip",
        }

    return {
        "language": "unknown",
        "manifest": None,
        "cmd": [],
        "binary": None,
        "install_hint": "No recognized project manifest (Cargo.toml, package.json, pyproject.toml, go.mod).",
        "output_file": "index.scip",
    }


def auto_index_and_ingest(
    workspace_root: str,
    graph_name: str = SYMBOLS_GRAPH_NAME,
    purge_existing: bool = True,
) -> Dict[str, Any]:
    """
    Discovers project type, generates index.scip if needed, and ingests into FalkorDB.
    """
    root = Path(workspace_root).resolve()
    scip_file = root / "index.scip"

    # Fast path: index.scip already exists
    if scip_file.exists():
        importer = SCIPImporter(graph_name=graph_name)
        res = importer.ingest_index(str(scip_file), purge_existing=purge_existing)
        res["auto_generated"] = False
        res["scip_file"] = str(scip_file)
        return res

    detector = detect_project_scip_indexer(str(root))
    if detector["language"] == "unknown":
        return {
            "status": "error",
            "message": f"Could not auto-detect project manifest in '{workspace_root}'. Please generate index.scip manually.",
        }

    binary = detector["binary"]
    if not shutil.which(binary):
        return {
            "status": "missing_indexer",
            "language": detector["language"],
            "manifest": detector["manifest"],
            "message": f"SCIP indexer '{binary}' is not installed on PATH.",
            "install_hint": detector["install_hint"],
        }

    # Run indexer
    print(f"[SCIP Auto] Running '{' '.join(detector['cmd'])}' in '{root}'...")
    try:
        proc = subprocess.run(
            detector["cmd"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120.0,
        )
        if proc.returncode != 0:
            return {
                "status": "error",
                "message": f"SCIP indexer failed with exit code {proc.returncode}: {proc.stderr[:500]}",
            }
    except Exception as exc:
        return {"status": "error", "message": f"Failed running SCIP indexer: {exc}"}

    if not scip_file.exists():
        return {
            "status": "error",
            "message": f"SCIP indexer succeeded but '{scip_file}' was not found.",
        }

    # Ingest into FalkorDB
    importer = SCIPImporter(graph_name=graph_name)
    res = importer.ingest_index(str(scip_file), purge_existing=purge_existing)
    res["auto_generated"] = True
    res["scip_file"] = str(scip_file)
    res["language"] = detector["language"]
    return res


def main():
    parser = argparse.ArgumentParser(description="Ingest SCIP Protobuf Index into FalkorDB")
    parser.add_argument("target", nargs="?", default=".", help="Path to index.scip or project root (with --auto)")
    parser.add_argument("--auto", action="store_true", help="Auto-detect project type and generate index.scip if missing")
    parser.add_argument("--graph", default=SYMBOLS_GRAPH_NAME, help="FalkorDB symbols graph name")
    parser.add_argument("--no-purge", action="store_true", help="Do not purge previous file relations")
    args = parser.parse_args()

    if args.auto:
        result = auto_index_and_ingest(args.target, graph_name=args.graph, purge_existing=not args.no_purge)
        if result.get("status") == "missing_indexer":
            print(f"⚠️  {result['message']}")
            print(f"   Install it via: {result['install_hint']}")
            return
        elif result.get("status") == "error":
            print(f"❌ Error: {result['message']}")
            return
    else:
        importer = SCIPImporter(graph_name=args.graph)
        result = importer.ingest_index(args.target, purge_existing=not args.no_purge)

    print(
        f"✅ SCIP Ingestion Complete: {result.get('documents_indexed', 0)} documents, "
        f"{result.get('symbols_indexed', 0)} symbols, {result.get('references_indexed', 0)} references, "
        f"{result.get('relationships_indexed', 0)} inter-symbol relations in {result.get('duration_seconds', 0)}s."
    )


if __name__ == "__main__":
    main()

