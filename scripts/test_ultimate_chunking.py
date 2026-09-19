"""
Comprehensive Test Suite for Cortex Ultimate Chunking Architecture.
"""
from __future__ import annotations

import sys
from pathlib import Path
from llama_index.core.schema import Document

from cortex.chunking import (
    ASTCodeSplitter,
    ConfigSectionSplitter,
    MarkdownSectionSplitter,
    SmartCodeNodeParser,
    count_tokens,
)
from cortex.chunking.ast_splitter import PY_LANG, TS_LANG


def test_token_counting():
    text = "def hello_world():\n    return 'cortex'\n"
    tokens = count_tokens(text)
    assert tokens > 0, "Token count must be positive"
    assert tokens < 30, "Short function should be fewer than 30 tokens"
    print(f"✓ test_token_counting passed: {tokens} tokens")


def test_tiny_stub_coalescing_and_skeleton():
    py_code = '''"""Module managing vector database client."""
import os
import sys

class VectorClient:
    """Manages connection to Qdrant vector store."""
    def __init__(self, host: str = "localhost", port: int = 6333):
        self.host = host
        self.port = port

    def get_host(self) -> str:
        return self.host

    def get_port(self) -> int:
        return self.port

    def is_active(self) -> bool:
        return True

    def execute_heavy_indexing(self, items: list) -> int:
        """Executes indexing on heavy items."""
        count = 0
        for item in items:
            count += 1
        return count
'''
    splitter = ASTCodeSplitter(
        language=PY_LANG,
        max_tokens=100,
        stub_threshold_tokens=40,
        coalesce_target_tokens=80,
    )
    chunks = splitter.split_code(py_code, file_path="cortex/db/client.py")
    
    # 1. Verify Skeleton chunk is present
    skeleton = [c for c in chunks if c.get("chunk_type") == "ast_skeleton"]
    assert len(skeleton) == 1, "Must generate exactly 1 skeleton chunk"
    sk_text = skeleton[0]["text"]
    assert "VectorClient" in sk_text, "Skeleton must list class name"
    assert "Module managing vector database client" in sk_text, "Skeleton must list module summary"
    assert "get_host" in sk_text and "is_active" in sk_text, "Skeleton must list method signatures"
    print("✓ test_skeleton_chunk passed")

    # 2. Verify Tiny-Stub Coalescing
    # __init__, get_host, get_port, is_active should coalesce together instead of being 4 separate chunks
    coalesced = [c for c in chunks if "methods:" in c.get("scope", "") or "coalesced" in c["text"].lower()]
    assert len(coalesced) >= 1, "Small stubs must be coalesced"
    coal_text = coalesced[0]["text"]
    assert "get_host" in coal_text and "get_port" in coal_text, "Coalesced chunk must contain multiple small methods"
    print("✓ test_tiny_stub_coalescing passed")

    # 3. Verify Contextual Preamble
    for c in chunks:
        if c.get("chunk_type") == "ast_code":
            assert "# File: cortex/db/client.py" in c["text"]
            assert "# Module: Module managing vector database client" in c["text"]
    print("✓ test_contextual_preamble passed")


def test_ast_statement_overlap():
    long_func = '''def process_pipeline(batch: list):
    """Deep pipeline with multiple control statements."""
    step1 = [x * 2 for x in batch]
    step2 = [x + 5 for x in step1]
    filtered = []
    for item in step2:
        if item % 2 == 0:
            filtered.append(item)
    try:
        val = sum(filtered)
        res = val / len(filtered)
    except Exception as err:
        res = 0.0
    output = {"result": res, "total": len(filtered)}
    return output
'''
    # Set small max_tokens to force splitting across statements
    splitter = ASTCodeSplitter(
        language=PY_LANG,
        max_tokens=65,
        overlap_tokens=25,
        stub_threshold_tokens=20,
    )
    chunks = splitter.split_code(long_func, file_path="cortex/pipeline.py")
    code_chunks = [c for c in chunks if c.get("chunk_type") == "ast_code"]
    assert len(code_chunks) >= 2, f"Expected multiple chunks, got {len(code_chunks)}"

    # Check that try block is kept whole and not severed
    has_try = any("try:" in c["text"] and "except" in c["text"] for c in code_chunks)
    assert has_try, "AST statement splitting must preserve intact try/except block"
    print("✓ test_ast_statement_overlap passed")


def test_config_section_splitter():
    yaml_text = """
version: '3.8'
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
  falkordb:
    image: falkordb/falkordb:latest
    ports:
      - "6379:6379"
"""
    splitter = ConfigSectionSplitter(max_tokens=100)
    chunks = splitter.split_config(yaml_text, file_path="docker-compose.yml")
    assert len(chunks) >= 1
    assert any("services" in c["scope"] for c in chunks)
    print("✓ test_config_section_splitter passed")


def test_smart_code_node_parser_end_to_end():
    parser = SmartCodeNodeParser(max_tokens=256, overlap_tokens=32)

    # Document 1: Python
    doc_py = Document(
        text="""def calculate_metrics(values: list) -> float:
    \"\"\"Calculates average.\"\"\"
    if not values:
        return 0.0
    return sum(values) / len(values)
""",
        metadata={"file_path": "cortex/metrics.py"},
    )

    # Document 2: Config
    doc_cfg = Document(
        text='{"host": "localhost", "port": 8000, "debug": false}',
        metadata={"file_path": "cortex/config.json"},
    )

    # Document 3: Markdown
    doc_md = Document(
        text="""# Cortex Guide
## Architecture
Cortex integrates Qdrant and FalkorDB.
## Usage
Run make start to begin.
""",
        metadata={"file_path": "README.md"},
    )

    nodes = parser.get_nodes_from_documents([doc_py, doc_cfg, doc_md])
    assert len(nodes) >= 3, "Expected at least 3 nodes"

    types = {n.metadata.get("chunk_type") for n in nodes}
    assert "ast_code" in types or "ast_skeleton" in types
    assert "config_section" in types
    assert "markdown_section" in types

    print(f"✓ test_smart_code_node_parser_end_to_end passed: {len(nodes)} nodes across types {types}")


if __name__ == "__main__":
    test_token_counting()
    test_tiny_stub_coalescing_and_skeleton()
    test_ast_statement_overlap()
    test_config_section_splitter()
    test_smart_code_node_parser_end_to_end()
    print("\n🎉 ALL ULTIMATE CHUNKING TESTS PASSED PERFECTLY!")
