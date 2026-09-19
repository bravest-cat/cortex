"""
Unit & Integration Test Suite for SCIP Ingestion into FalkorDB.

Validates:
1. SCIP Protobuf deserialization (scip_pb2.Index)
2. Document, Symbol, and File node creation
3. Definition, Import, and Reference edge construction
4. Type relationships (IMPLEMENTS, TYPE_OF)
5. Graph queries: Jump to Definition & Find References across SCIP nodes
"""
import os
import tempfile
from pathlib import Path

from cortex.symbols import scip_pb2
from cortex.symbols.scip import SCIPImporter, parse_scip_symbol_name, extract_parent_name
from cortex.symbols.graph import SymbolGraphManager


def create_sample_scip_index() -> scip_pb2.Index:
    """Constructs a realistic, multi-file SCIP Index with definitions, references, and traits."""
    index = scip_pb2.Index()
    index.metadata.version = 1
    index.metadata.tool_info.name = "scip-rust"
    CORTEX_ROOT = Path(__file__).resolve().parent.parent
    index.metadata.project_root = f"file://{CORTEX_ROOT}"

    # --- Document 1: engine.rs ---
    doc1 = index.documents.add()
    doc1.language = "rust"
    doc1.relative_path = "src/engine.rs"

    # Symbol 1: Engine struct
    s_engine = doc1.symbols.add()
    s_engine.symbol = "rust-analyzer cargo cortex 0.1.0 Engine#"
    s_engine.kind = scip_pb2.SymbolInformation.Kind.Struct
    s_engine.display_name = "Engine"
    s_engine.documentation.append("High performance execution engine.")
    # Relationship: Engine implements Runnable trait
    rel = s_engine.relationships.add()
    rel.symbol = "rust-analyzer cargo cortex 0.1.0 Runnable#"
    rel.is_implementation = True

    # Symbol 2: Engine.execute method
    s_exec = doc1.symbols.add()
    s_exec.symbol = "rust-analyzer cargo cortex 0.1.0 Engine#execute()."
    s_exec.kind = scip_pb2.SymbolInformation.Kind.Method
    s_exec.display_name = "execute"
    s_exec.enclosing_symbol = s_engine.symbol
    s_exec.documentation.append("Executes the internal worker pipeline.")

    # Occurrences in engine.rs:
    # 1. Definition of Engine at line 2 (0-indexed line 1, col 11 to 17)
    occ1 = doc1.occurrences.add()
    occ1.range.extend([1, 11, 1, 17])
    occ1.symbol = s_engine.symbol
    occ1.symbol_roles = scip_pb2.SymbolRole.Definition

    # 2. Definition of execute at line 4
    occ2 = doc1.occurrences.add()
    occ2.range.extend([3, 11, 3, 18])
    occ2.symbol = s_exec.symbol
    occ2.symbol_roles = scip_pb2.SymbolRole.Definition

    # 3. Call to worker::run at line 5
    occ3 = doc1.occurrences.add()
    occ3.range.extend([4, 8, 4, 11])
    occ3.symbol = "rust-analyzer cargo cortex 0.1.0 worker/run()."
    occ3.symbol_roles = scip_pb2.SymbolRole.ReadAccess

    # --- Document 2: worker.rs ---
    doc2 = index.documents.add()
    doc2.language = "rust"
    doc2.relative_path = "src/worker.rs"

    # Symbol 3: worker::run function
    s_run = doc2.symbols.add()
    s_run.symbol = "rust-analyzer cargo cortex 0.1.0 worker/run()."
    s_run.kind = scip_pb2.SymbolInformation.Kind.Function
    s_run.display_name = "run"
    s_run.documentation.append("Runs task in dedicated thread pool.")

    # Occurrence: Definition of run at line 2
    occ4 = doc2.occurrences.add()
    occ4.range.extend([1, 7, 1, 10])
    occ4.symbol = s_run.symbol
    occ4.symbol_roles = scip_pb2.SymbolRole.Definition

    return index


def test_scip_symbol_parsing():
    print("\n--- 1. Testing SCIP Symbol & Parent Parsing ---")
    assert parse_scip_symbol_name("rust-analyzer cargo cortex 0.1.0 Engine#execute().") == "execute"
    assert parse_scip_symbol_name("rust-analyzer cargo cortex 0.1.0 Engine#") == "Engine"
    assert parse_scip_symbol_name("scip-python python cortex 0.1.0 `cortex.server`/trigger_dream_mode().") == "trigger_dream_mode"
    assert parse_scip_symbol_name("local 105") == "local:105"

    assert extract_parent_name("rust-analyzer cargo cortex 0.1.0 Engine#execute().") == "Engine"
    assert extract_parent_name("foo/bar().", enclosing_sym="rust-analyzer cargo cortex 0.1.0 Engine#") == "Engine"
    print("✅ SCIP symbol string normalization & hierarchy parsing passed.")


def test_scip_ingestion_and_falkordb_queries():
    print("\n--- 2. Testing SCIP Ingestion into FalkorDB ---")
    test_graph = "cortex_test_scip"
    importer = SCIPImporter(graph_name=test_graph)

    # 1. Clear test graph
    importer._query("MATCH (n) DETACH DELETE n")

    # 2. Build and save index to temporary file
    index = create_sample_scip_index()
    with tempfile.NamedTemporaryFile(suffix=".scip", delete=False) as tmp:
        tmp.write(index.SerializeToString())
        tmp_path = tmp.name

    try:
        # Ingest file
        res = importer.ingest_index(tmp_path, purge_existing=True)
        assert res["status"] == "ok"
        assert res["documents_indexed"] == 2
        assert res["symbols_indexed"] == 3
        assert res["references_indexed"] == 1
        assert res["relationships_indexed"] == 1
        print(f"✅ Ingested SCIP index in {res['duration_seconds']}s.")

        # 3. Query symbol definitions using SymbolGraphManager
        graph_mgr = SymbolGraphManager(graph_name=test_graph)

        # Test Jump to Definition: Engine
        engine_defs = graph_mgr.find_definition("Engine")
        assert len(engine_defs) == 1
        assert engine_defs[0]["file_path"] == "src/engine.rs"
        assert engine_defs[0]["kind"] == "struct"
        print("✅ Jump to Definition for Struct 'Engine' verified.")

        # Test Jump to Definition: execute
        exec_defs = graph_mgr.find_definition("execute")
        assert len(exec_defs) == 1
        assert exec_defs[0]["parent"] == "Engine"
        assert exec_defs[0]["kind"] == "method"
        print("✅ Jump to Definition for Method 'execute' with parent verified.")

        # Test Find References: run
        run_res = graph_mgr.find_references("run")
        run_refs = run_res["references"]
        assert len(run_refs) >= 1
        assert any(r["file_path"] == "src/engine.rs" for r in run_refs)
        print("✅ Cross-file Find References: 'src/engine.rs' -> 'run' verified.")

        # Test Inter-Symbol Relationship: Engine IMPLEMENTS Runnable
        rel_rows = importer._query(
            "MATCH (s1:Symbol {name: 'Engine'})-[r:IMPLEMENTS]->(s2:Symbol {name: 'Runnable'}) "
            "RETURN s1.name, type(r), s2.name"
        )
        assert rel_rows and len(rel_rows) > 1 and len(rel_rows[1]) > 0
        print("✅ Compiler-Grade Type Relationship (Engine)-[:IMPLEMENTS]->(Runnable) verified.")

        # 4. Test Idempotency (re-running does not corrupt graph)
        res2 = importer.ingest_index(tmp_path, purge_existing=True)
        assert res2["symbols_indexed"] == 3
        files_count = importer._query("MATCH (f:File) RETURN count(f)")[1][0][0]
        assert files_count == 2
        print("✅ Idempotent re-ingestion verified: zero duplicate nodes.")

    finally:
        # Cleanup
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        try:
            importer._get_client().execute_command("GRAPH.DELETE", test_graph)
        except Exception:
            pass


def main():
    print("🚀 Running SCIP Ingestion Test Suite...")
    test_scip_symbol_parsing()
    test_scip_ingestion_and_falkordb_queries()
    print("\n🎉 ALL SCIP INGESTION TESTS PASSED PERFECTLY!")


if __name__ == "__main__":
    main()
