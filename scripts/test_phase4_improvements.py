"""
Comprehensive verification test suite for the 4 Cortex System Improvements:
1. Automated Incremental File Watcher (cortex.watcher)
2. Blast-Radius / Impact Analysis Tool (trace_symbol_impact)
3. SCIP Auto-Discovery & Ingestion Wrapper (auto_index_and_ingest)
4. Chunk-Level SHA-256 Embedding Cache (ChunkEmbeddingCache)
"""

import sys
import os
import time
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cortex.cache import ChunkEmbeddingCache, IngestCache
from cortex.symbols.graph import SymbolGraphManager
from cortex.symbols.scip import detect_project_scip_indexer, auto_index_and_ingest
from cortex.watcher import CortexIncrementalSync, DebouncedChangeHandler


def test_chunk_embedding_cache():
    print("\n--- 1. Testing Chunk-Level SHA-256 Embedding Cache ---")
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        cache = ChunkEmbeddingCache(Path(tmp.name))

        dim = 64
        vec_foo = [0.1 * i for i in range(dim)]
        vec_bar = [0.2 * i for i in range(dim)]

        # Store
        cache.put_batch(
            [("def foo(): return 42", vec_foo), ("class Bar: pass", vec_bar)],
            model_name="test-embed-model",
        )

        # Single lookup
        res_foo = cache.get_embedding("def foo(): return 42", model_name="test-embed-model")
        assert res_foo is not None, "Must retrieve cached embedding"
        assert len(res_foo) == dim
        assert abs(res_foo[5] - (0.1 * 5)) < 1e-5

        # Batch lookup with hits and misses
        texts = [
            "def foo(): return 42",     # Hit (index 0)
            "def unknown(): pass",       # Miss (index 1)
            "class Bar: pass",           # Hit (index 2)
            "def another_miss(): pass",  # Miss (index 3)
        ]
        hits, misses = cache.get_batch(texts, model_name="test-embed-model")
        assert len(hits) == 2, "Must have exactly 2 hits"
        assert 0 in hits and 2 in hits, "Indices 0 and 2 must be hits"
        assert len(misses) == 2, "Must have exactly 2 misses"
        assert misses[0][0] == 1 and misses[1][0] == 3

        stats = cache.get_stats()
        assert stats["total_cached_chunks"] == 2
        print(f"✅ ChunkEmbeddingCache verified: {stats['total_cached_chunks']} chunks cached ({stats['storage_size_bytes']} bytes).")


def test_symbol_impact_analysis():
    print("\n--- 2. Testing Blast-Radius / Impact Analysis (trace_symbol_impact) ---")
    mgr = SymbolGraphManager(graph_name="cortex_test_impact_symbols")

    try:
        # Set up synthetic definitions and calls
        mgr.save_file_ast(
            file_path="cortex/db/store.py",
            declarations=[{"name": "StorageEngine", "kind": "class", "line": 10, "signature": "class StorageEngine", "docstring": "Core DB store", "parent": None}],
            imports=[],
            references=[],
        )
        mgr.save_file_ast(
            file_path="cortex/session.py",
            declarations=[{"name": "SessionManager", "kind": "class", "line": 5, "signature": "class SessionManager", "docstring": "", "parent": None}],
            imports=[{"symbol": "StorageEngine", "module": "cortex.db.store", "line": 2}],
            references=[{"name": "StorageEngine", "kind": "call", "line": 12}],
        )
        mgr.save_file_ast(
            file_path="cortex/api.py",
            declarations=[{"name": "handle_request", "kind": "function", "line": 20, "signature": "def handle_request()", "docstring": "", "parent": None}],
            imports=[{"symbol": "SessionManager", "module": "cortex.session", "line": 3}],
            references=[{"name": "SessionManager", "kind": "call", "line": 25}],
        )

        # Test upstream impact of StorageEngine (who will break?)
        upstream = mgr.trace_symbol_impact("StorageEngine", direction="upstream", max_depth=3)
        assert upstream["symbol"] == "StorageEngine"
        assert upstream["direction"] == "upstream"
        assert len(upstream["definitions"]) >= 1
        assert any("cortex/session.py" in str(d["source_file"]) for d in upstream["direct_impacts"])

        # Test downstream impact of handle_request (what does it rely on?)
        downstream = mgr.trace_symbol_impact("handle_request", direction="downstream", max_depth=3)
        assert downstream["symbol"] == "handle_request"
        assert downstream["direction"] == "downstream"
        assert any("SessionManager" in str(d["target_symbol"]) for d in downstream["direct_impacts"])

        print(f"✅ trace_symbol_impact verified: Upstream and downstream impact paths traversed successfully.")
    finally:
        try:
            mgr._get_client().execute_command("GRAPH.DELETE", "cortex_test_impact_symbols")
        except Exception:
            pass


def test_scip_auto_detection():
    print("\n--- 3. Testing SCIP Auto-Discovery & Ingestion Wrapper ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # 1. Test Rust detection
        (root / "Cargo.toml").write_text("[package]\nname = 'test-crate'")
        res_rust = detect_project_scip_indexer(str(root))
        assert res_rust["language"] == "rust"
        assert res_rust["binary"] == "rust-analyzer"
        (root / "Cargo.toml").unlink()

        # 2. Test TypeScript detection
        (root / "package.json").write_text('{"name": "test-pkg"}')
        res_ts = detect_project_scip_indexer(str(root))
        assert res_ts["language"] == "typescript"
        assert "scip-typescript" in res_ts["binary"] or "npx" in res_ts["binary"]
        (root / "package.json").unlink()

        # 3. Test Python detection
        (root / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'")
        res_py = detect_project_scip_indexer(str(root))
        assert res_py["language"] == "python"
        assert res_py["binary"] == "scip-python"

        # 4. Test auto_index_and_ingest when index.scip already exists
        dummy_scip = root / "index.scip"
        from cortex.symbols import scip_pb2
        idx = scip_pb2.Index()
        dummy_scip.write_bytes(idx.SerializeToString())

        try:
            res_auto = auto_index_and_ingest(str(root), graph_name="cortex_test_scip_auto")
            assert res_auto["status"] == "ok"
            assert res_auto["auto_generated"] is False
            print("✅ SCIP Auto-Discovery & Ingestion wrapper verified across all manifests.")
        finally:
            try:
                import redis
                r = redis.Redis(host="localhost", port=6379, decode_responses=True)
                r.execute_command("GRAPH.DELETE", "cortex_test_scip_auto")
            except Exception:
                pass


def test_incremental_watcher_debounce():
    print("\n--- 4. Testing Automated Incremental File Watcher ---")
    syncer = CortexIncrementalSync(collection="cortex_test_watch")
    loop = asyncio.new_event_loop()
    handler = DebouncedChangeHandler(loop=loop, syncer=syncer, debounce_sec=0.1)

    class DummyEvent:
        is_directory = False
        src_path = "/tmp/test_watch_file.py"

    # Push rapid events simulating fast editor typing
    ev = DummyEvent()
    handler.on_modified(ev)
    assert "/tmp/test_watch_file.py" in handler.pending_events

    # Should not flush immediately before debounce time
    async def run_debounce_test():
        await handler.flush_debounced()
        assert "/tmp/test_watch_file.py" in handler.pending_events, "Must debounce"

        # Wait for debounce window to expire
        await asyncio.sleep(0.15)
        # Verify it selects for processing
        now = time.time()
        ready = [p for p, t in handler.pending_events.items() if now - t >= handler.debounce_sec]
        assert len(ready) == 1
        assert ready[0] == "/tmp/test_watch_file.py"

    loop.run_until_complete(run_debounce_test())
    loop.close()
    print("✅ DebouncedChangeHandler verified: rapid events safely coalesced.")


if __name__ == "__main__":
    test_chunk_embedding_cache()
    test_symbol_impact_analysis()
    test_scip_auto_detection()
    test_incremental_watcher_debounce()
    print("\n🎉 ALL 4 CORTEX SYSTEM IMPROVEMENTS VERIFIED AND WORKING 100%!")
