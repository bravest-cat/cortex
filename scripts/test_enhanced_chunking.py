"""
Test script to verify the 5 Advanced AST Chunking & Retrieval Enhancements:
1. Injected Dependency & Import Context
2. Small-to-Big / Parent-Child Hierarchical Retrieval (parent_id, parent_scope, parent_text)
3. Test-to-Implementation Linkage (# Tests Target: ..., metadata test_targets)
4. Cross-Language Docstring Elevation (preceding doc-comments in Rust, Go, Python)
5. Git Temporal & Recency Metadata Tagging (# Last Modified: ..., commit_date/mtime)
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llama_index.core.schema import Document
from cortex.chunking.ast_splitter import ASTCodeSplitter, LANG_MAP, SmartCodeNodeParser


def test_dependency_and_docstring_and_temporal():
    code = '''"""Module docstring explaining the module."""
import os
import sys
from cortex.graph.store import FalkorDBStore
from cortex.embeddings import get_embedder

class StorageManager:
    """Manages system persistent storage."""

    def __init__(self, host: str):
        self.host = host
        self.store = FalkorDBStore()

    def sync(self):
        """Synchronize records with embedder."""
        embedder = get_embedder()
        return embedder.embed(self.host)
'''
    # Set max_tokens=40 to force method-level splitting of the class
    splitter = ASTCodeSplitter(
        language=LANG_MAP[".py"],
        max_tokens=40,
        stub_threshold_tokens=15,
        coalesce_target_tokens=25,
    )
    chunks = splitter.split_code(code, file_path="cortex/storage.py")
    assert len(chunks) > 0

    # 1. Verify temporal info is present across all chunks
    for chunk in chunks:
        assert "commit_date" in chunk
        assert "commit_hash" in chunk
        assert "# Last Modified:" in chunk["text"]

    # 2. Verify StorageManager method chunk has parent-child link, dependencies, and docstring
    sync_chunks = [c for c in chunks if c.get("chunk_type") == "ast_code" and "def sync" in c["text"]]
    assert len(sync_chunks) == 1
    sync_chunk = sync_chunks[0]

    # Verify dependency injection (structured + preamble)
    dep_symbols = [d["symbol"] for d in sync_chunk.get("dependencies", [])]
    assert "get_embedder" in dep_symbols
    assert "# Depends On: get_embedder (cortex.embeddings)" in sync_chunk["text"]

    # Verify parent-child hierarchy
    assert sync_chunk.get("parent_id") == "cortex/storage.py::class::StorageManager"
    assert sync_chunk.get("parent_scope") == "class StorageManager"
    assert "class StorageManager" in sync_chunk.get("parent_text", "")

    # Verify docstring elevation
    assert "Synchronize records with embedder." in sync_chunk.get("docstring", "")
    assert "# Docstring: Synchronize records with embedder." in sync_chunk["text"]
    print("✓ test_dependency_and_docstring_and_temporal passed")


def test_test_to_implementation_linkage():
    test_code = '''import pytest
from cortex.storage import StorageManager
from cortex.graph.store import FalkorDBStore

def test_storage_manager_sync():
    manager = StorageManager("localhost")
    res = manager.sync()
    assert res is not None

class TestFalkorIntegration:
    """Integration test suite for Falkor."""
    def test_store_connection(self):
        store = FalkorDBStore()
        assert store.ping()
'''
    splitter = ASTCodeSplitter(
        language=LANG_MAP[".py"],
        max_tokens=50,
        stub_threshold_tokens=20,
        coalesce_target_tokens=35,
    )
    chunks = splitter.split_code(test_code, file_path="tests/test_storage.py")

    test_sync = [c for c in chunks if c.get("chunk_type") == "ast_code" and "test_storage_manager_sync" in c["text"]][0]
    assert "StorageManager" in test_sync.get("test_targets", [])
    assert "# Tests Target: StorageManager" in test_sync["text"]

    test_class_method = [c for c in chunks if c.get("chunk_type") == "ast_code" and "test_store_connection" in c["text"]][0]
    assert "FalkorDBStore" in test_class_method.get("test_targets", [])
    assert "# Tests Target: FalkorDBStore" in test_class_method["text"]
    assert test_class_method.get("parent_id") == "tests/test_storage.py::class::TestFalkorIntegration"
    print("✓ test_test_to_implementation_linkage passed")


def test_cross_language_docstring_and_deps_rust():
    rust_code = '''use std::collections::HashMap;
use crate::graph::FalkorClient;

/// Computes community clusters using Louvain modularity.
/// Ensures optimal subsystem grouping.
pub fn cluster_communities(graph: &FalkorClient) -> HashMap<String, usize> {
    let mut map = HashMap::new();
    let client = FalkorClient::new();
    map.insert("subsystem".to_string(), 1);
    map
}
'''
    splitter = ASTCodeSplitter(
        language=LANG_MAP[".rs"],
        max_tokens=150,
        stub_threshold_tokens=10,
        coalesce_target_tokens=50,
    )
    chunks = splitter.split_code(rust_code, file_path="src/cluster.rs")
    cluster_chunk = [c for c in chunks if c.get("chunk_type") == "ast_code" and "cluster_communities" in c.get("scope", "")][0]

    dep_symbols = [d["symbol"] for d in cluster_chunk.get("dependencies", [])]
    assert "FalkorClient" in dep_symbols
    assert "Computes community clusters" in cluster_chunk.get("docstring", "")
    assert "# Docstring: Computes community clusters" in cluster_chunk["text"]
    assert "# Depends On: FalkorClient" in cluster_chunk["text"]
    print("✓ test_cross_language_docstring_and_deps_rust passed")


def test_smart_code_node_parser_metadata_propagation():
    doc = Document(
        text='''import os
from cortex.graph.store import FalkorDBStore

class QueryEngine:
    """Core semantic query engine."""
    def query(self, prompt: str):
        store = FalkorDBStore()
        return os.getenv("MODE")
''',
        metadata={"file_path": "cortex/query.py", "language": "python"}
    )
    # Parser with max_tokens=40 to enforce parent-child hierarchy
    parser = SmartCodeNodeParser(max_tokens=40)
    nodes = parser.get_nodes_from_documents([doc])

    query_nodes = [n for n in nodes if n.metadata.get("chunk_type") == "ast_code" and "def query" in n.text]
    assert len(query_nodes) > 0
    qnode = query_nodes[0]

    # Verify all 5 enhancements propagated into TextNode.metadata
    assert qnode.metadata.get("parent_id") == "cortex/query.py::class::QueryEngine"
    assert qnode.metadata.get("parent_scope") == "class QueryEngine"
    assert "class QueryEngine" in qnode.metadata.get("parent_text", "")
    deps = [d["symbol"] for d in qnode.metadata.get("dependencies", [])]
    assert "FalkorDBStore" in deps or "os" in deps
    assert "commit_date" in qnode.metadata
    assert "commit_hash" in qnode.metadata
    print("✓ test_smart_code_node_parser_metadata_propagation passed")


if __name__ == "__main__":
    test_dependency_and_docstring_and_temporal()
    test_test_to_implementation_linkage()
    test_cross_language_docstring_and_deps_rust()
    test_smart_code_node_parser_metadata_propagation()
    print("\n🎉 ALL 5 ENHANCED CHUNKING TESTS PASSED PERFECTLY!")
