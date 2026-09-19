"""
Unit & Integration Test Suite for Cortex Dashboard Enhancements.
Tests SCIP edges in /api/graph, Blast Radius analysis in /api/symbols/impact,
chunk cache telemetry, and watcher status.
"""
from pathlib import Path
from starlette.testclient import TestClient
from cortex.dashboard.app import app

client = TestClient(app)

def test_cache_stats_endpoint():
    resp = client.get("/api/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert "chunk_cache" in data
    assert "total_indexed_files" in data
    print("✅ /api/cache/stats returns valid schema")

def test_watcher_status_endpoint():
    resp = client.get("/api/watcher/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert "watcher" in data
    print("✅ /api/watcher/status returns valid schema")

def test_symbols_autocomplete_endpoint():
    resp = client.get("/api/symbols/autocomplete?q=Symbol&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert isinstance(data.get("items"), list)
    print(f"✅ /api/symbols/autocomplete returned {len(data['items'])} items")

def test_symbol_blast_radius_symbol():
    resp = client.get("/api/symbols/impact?target=SymbolGraphManager&direction=upstream&max_depth=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert data.get("target") == "SymbolGraphManager"
    assert "summary" in data
    assert "nodes" in data
    assert "edges" in data
    assert "tree" in data
    summary = data["summary"]
    assert summary.get("type") == "symbol"
    assert summary.get("direction") == "upstream"
    print(f"✅ /api/symbols/impact (symbol upstream) returned {len(data['nodes'])} nodes, {len(data['edges'])} edges")

def test_symbol_blast_radius_file():
    target_fp = str(Path(__file__).resolve().parent.parent / "cortex" / "symbols" / "graph.py")
    resp = client.get(f"/api/symbols/impact?file_path={target_fp}")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert "summary" in data
    assert "nodes" in data
    assert "edges" in data
    summary = data["summary"]
    assert summary.get("type") == "file"
    print(f"✅ /api/symbols/impact (file) returned {len(data['nodes'])} nodes, {len(data['edges'])} edges")

def test_graph_endpoint_with_scip():
    resp = client.get("/api/graph?graph_type=symbols&clean=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert "nodes" in data
    assert "edges" in data
    print(f"✅ /api/graph (symbols) returned {len(data['nodes'])} nodes, {len(data['edges'])} edges")

if __name__ == "__main__":
    test_cache_stats_endpoint()
    test_watcher_status_endpoint()
    test_symbols_autocomplete_endpoint()
    test_symbol_blast_radius_symbol()
    test_symbol_blast_radius_file()
    test_graph_endpoint_with_scip()
    print("\n🎉 ALL DASHBOARD ENHANCEMENT TESTS PASSED!")
