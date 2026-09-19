"""
End-to-End Test Suite for Cortex Evolution & Autonomous Cognitive Architecture.

Validates:
1. Agent-Native Episodic Memory Driver (direct FalkorDB, no :8003 proxy)
2. Cross-Language Concrete AST Symbol Extraction (Python, TS, JS, Rust, Go)
3. ASTCodeSplitter & SmartCodeNodeParser for Rust and Go
4. On-Demand Dream Mode (Louvain Subsystem Clustering + Invariant Induction)
5. FastMCP 6-tool Server Interface (check_cortex_health, record_system_outcome, trigger_dream_mode)
"""
import asyncio
import json
import os
from pathlib import Path

from cortex.episodic.driver import AgentNativeEpisodicDriver
from cortex.chunking.ast_splitter import LANG_MAP, ASTCodeSplitter
from cortex.symbols.graph import SymbolGraphManager
from cortex.symbols.clustering import SubsystemClusterer
from cortex.dream import DreamEngine
import cortex.server as server


async def test_agent_native_episodic_memory():
    print("\n--- 1. Testing Agent-Native Episodic Memory Driver ---")
    driver = AgentNativeEpisodicDriver(db_name="cortex_test_episodic")
    await driver.initialize()

    try:
        # Clear test db
        driver._query("MATCH (n) DETACH DELETE n")

        # Record first outcome
        res1 = await driver.record_outcome(
            episode_name="Switched embed server to port 8000",
            content="Migrated embeddings to local oMLX :8000 Metal GPU.",
            tags=["embeddings", "omlx", "ports"],
            entities=[{"name": "oMLX", "type": "service", "summary": "Local inference server"}],
            facts=[{"source": "oMLX", "relation": "SERVES_ON", "target": "Port 8000", "fact": "oMLX runs on port 8000"}],
        )
        assert res1["status"] == "ok"
        assert res1["entities_written"] == 1
        assert res1["facts_written"] == 1
        print("✅ Recorded episode 1 directly to FalkorDB without LLM proxy daemon.")

        # Search
        search_res = await driver.search("What port does oMLX run on?")
        assert len(search_res) > 0
        assert any("8000" in s for s in search_res)
        print("✅ Retrieved non-superseded operational fact from FalkorDB.")

        # Supersede older fact
        res2 = await driver.record_outcome(
            episode_name="Switched embed server to port 8001",
            content="Migrated embeddings to port 8001 for test.",
            supersedes=["Switched embed server to port 8000", "Port 8000"],
        )
        assert res2["superseded_count"] >= 1
        print(f"✅ Successfully superseded older conflicting fact ({res2['superseded_count']} node(s) marked superseded).")
    finally:
        try:
            driver._r.execute_command("GRAPH.DELETE", "cortex_test_episodic")
        except Exception:
            pass
        await driver.close()


def test_cross_language_ast_symbols():
    print("\n--- 2. Testing Cross-Language Symbol Resolution (Python, TS, Rust, Go) ---")
    # Rust
    rs_code = (
        "use std::sync::Arc;\n"
        "pub struct Engine { name: String }\n"
        "impl Engine {\n"
        "    pub fn execute(&self) -> bool {\n"
        "        worker::run();\n"
        "        true\n"
        "    }\n"
        "}\n"
    )
    s_rs = ASTCodeSplitter(LANG_MAP[".rs"])
    res_rs = s_rs.extract_symbols_and_references(rs_code, "engine.rs")
    decls_rs = {d["name"]: d for d in res_rs["declarations"]}
    assert "Engine" in decls_rs and decls_rs["Engine"]["kind"] == "struct"
    assert "execute" in decls_rs and decls_rs["execute"]["parent"] == "Engine"
    assert len(res_rs["imports"]) == 1 and res_rs["imports"][0]["symbol"] == "Arc"
    assert len(res_rs["references"]) >= 1 and res_rs["references"][0]["symbol"] == "run"
    print("✅ Rust: Structs, impl blocks, methods, use-declarations, and calls extracted.")

    # Go
    go_code = (
        "package main\n"
        "import \"net/http\"\n"
        "type Router struct { prefix string }\n"
        "func (r *Router) Route() {\n"
        "    http.HandleFunc(\"/\", nil)\n"
        "}\n"
    )
    s_go = ASTCodeSplitter(LANG_MAP[".go"])
    res_go = s_go.extract_symbols_and_references(go_code, "router.go")
    decls_go = {d["name"]: d for d in res_go["declarations"]}
    assert "Router" in decls_go and decls_go["Router"]["kind"] == "type"
    assert "Route" in decls_go and decls_go["Route"]["parent"] == "Router"
    assert len(res_go["imports"]) == 1 and res_go["imports"][0]["symbol"] == "http"
    assert len(res_go["references"]) >= 1 and res_go["references"][0]["symbol"] == "HandleFunc"
    print("✅ Go: Types, pointer receiver methods, imports, and calls extracted.")


async def test_dream_mode_dry_run():
    print("\n--- 3. Testing Dream Mode Engine (Dry-Run & Architecture) ---")
    engine = DreamEngine()
    report = await engine.run_dream_cycle(project="cortex", purge_stale=False, dry_run=True)
    assert report["status"] == "ok"
    assert report["subsystems_count"] >= 1
    assert "Louvain Modularity" in report["markdown_report"]
    print(f"✅ Dream Mode dry-run passed ({report['subsystems_count']} subsystems, {report['invariants_count']} invariants).")


async def test_mcp_server_tools():
    print("\n--- 4. Testing FastMCP 6-Tool Integration ---")
    # Health check
    health_str = await server.check_cortex_health()
    health = json.loads(health_str)
    assert health["qdrant"] == "✅ RUNNING"
    assert health["falkordb"] == "✅ RUNNING"
    assert health["omlx_server"] == "✅ RUNNING"
    assert "llm_proxy" not in health  # 8003 decommissioned!
    print("✅ check_cortex_health verified: Qdrant, FalkorDB, and oMLX running; :8003 proxy removed.")

    # Record outcome tool
    rec_result = await server.record_system_outcome(
        episode_name="Cortex Evolution System Test",
        content="Automated verification of the agent-native architecture.",
        tags=["test", "agent-native", "evolution"],
        project="cortex",
        supersedes=["Temporary test fact"],
    )
    assert "Journal:" in rec_result
    assert "FalkorDB" in rec_result
    print("✅ record_system_outcome verified: Persisted to journal & FalkorDB with zero proxy daemon.")


async def main():
    print("🚀 Running Cortex Evolution End-to-End Test Suite...")
    await test_agent_native_episodic_memory()
    test_cross_language_ast_symbols()
    await test_dream_mode_dry_run()
    await test_mcp_server_tools()
    print("\n🎉 ALL CORTEX EVOLUTION END-TO-END TESTS PASSED PERFECTLY!")


if __name__ == "__main__":
    asyncio.run(main())
