"""
Automated Test Suite for Cortex Markdown & Knowledge Vault Ingestion.

Tests:
1. MarkdownSymbolExtractor (frontmatter, headings, wiki-links, standard links, tags)
2. SymbolGraphManager (save_markdown_ast, backlinks, blast radius, statistics)
3. SmartCodeNodeParser (markdown breadcrumb hierarchy & tags metadata)
4. Teardown of test graphs
"""
import sys
from pathlib import Path

# Add project root to sys.path
CORTEX_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORTEX_ROOT))

from llama_index.core.schema import Document
from cortex.chunking import SmartCodeNodeParser
from cortex.symbols import MarkdownSymbolExtractor, SymbolGraphManager


def test_markdown_extractor():
    print("🧪 Testing MarkdownSymbolExtractor...")
    raw_md = """---
title: Cognitive Architecture Vault
tags: [ai, memory, falkordb]
author: Antigravity Team
---
# Cognitive Architecture Vault
This note links to [[Episodic Memory]] and [Qdrant Configuration](config/qdrant.yaml).
See also [[Vector Embeddings#Sparse Rerank|Sparse Search]].
#knowledge-graph #omlx

## Storage Tier
Here we describe FalkorDB graph storage.

### Subsystem Details
FalkorDB stores graphiti nodes and SCIP symbols.
"""
    extractor = MarkdownSymbolExtractor(workspace_root=str(CORTEX_ROOT))
    res = extractor.extract(raw_md, file_path=f"{CORTEX_ROOT}/vault/architecture.md")

    assert res["title"] == "Cognitive Architecture Vault", f"Unexpected title: {res['title']}"
    assert "ai" in res["tags"] and "memory" in res["tags"] and "omlx" in res["tags"], f"Tags missing: {res['tags']}"
    assert len(res["sections"]) == 3, f"Expected 3 sections, got {len(res['sections'])}"
    assert res["sections"][0]["name"] == "Cognitive Architecture Vault"
    assert res["sections"][1]["heading_path"] == "Cognitive Architecture Vault > Storage Tier"
    assert res["sections"][2]["heading_path"] == "Cognitive Architecture Vault > Storage Tier > Subsystem Details"

    # Verify Links
    link_types = [l["link_type"] for l in res["links"]]
    assert "wiki_link" in link_types
    assert "markdown_link" in link_types

    resolved_links = [l for l in res["links"] if l["is_resolved"]]
    assert len(resolved_links) >= 1, "Expected config/qdrant.yaml link to be resolved"
    assert "qdrant.yaml" in resolved_links[0]["target_path"]
    print("  ✅ MarkdownSymbolExtractor passed!")


def test_symbol_graph_markdown_integration():
    print("🧪 Testing SymbolGraphManager save_markdown_ast & backlinks...")
    test_graph = "cortex_test_markdown_symbols"
    mgr = SymbolGraphManager(graph_name=test_graph)
    mgr.clear()

    try:
        extractor = MarkdownSymbolExtractor(workspace_root=str(CORTEX_ROOT))
        note1 = """---
title: Distributed Systems
tags: [systems, raft]
---
# Distributed Systems
Discusses consensus and links to [[Fault Tolerance]] and [[Storage Engine]].
"""
        note2 = """---
title: Storage Engine
tags: [storage, database]
---
# Storage Engine
Detailed storage guide. Links to [[Fault Tolerance]].
"""
        p1 = extractor.extract(note1, f"{CORTEX_ROOT}/vault/distributed.md")
        p2 = extractor.extract(note2, f"{CORTEX_ROOT}/vault/storage.md")

        mgr.save_markdown_ast(
            file_path=p1["file_path"],
            title=p1["title"],
            frontmatter=p1["frontmatter"],
            sections=p1["sections"],
            declarations=p1["declarations"],
            links=p1["links"],
            tags=p1["tags"],
        )
        mgr.save_markdown_ast(
            file_path=p2["file_path"],
            title=p2["title"],
            frontmatter=p2["frontmatter"],
            sections=p2["sections"],
            declarations=p2["declarations"],
            links=p2["links"],
            tags=p2["tags"],
        )

        stats = mgr.get_statistics()
        assert stats["documents"] == 2, f"Expected 2 documents, got {stats['documents']}"
        assert stats["links"] == 3, f"Expected 3 links, got {stats['links']}"
        assert stats["tags"] >= 3, f"Expected >= 3 tags, got {stats['tags']}"

        # Test find_definition on document title
        defs = mgr.find_definition("Distributed Systems")
        assert any(d["kind"] == "document" for d in defs), "Expected document definition"

        # Test backlinks for Fault Tolerance concept
        refs_ft = mgr.find_references("Fault Tolerance")
        assert len(refs_ft["backlinks"]) == 2, f"Expected 2 backlinks to Fault Tolerance, got {len(refs_ft['backlinks'])}"

        # Test backlinks for Storage Engine note
        refs_se = mgr.find_references("Storage Engine")
        assert len(refs_se["backlinks"]) >= 1, f"Expected backlink from Distributed Systems to Storage Engine"
        assert any("distributed.md" in bl["file_path"] for bl in refs_se["backlinks"])

        # Test blast radius
        impact = mgr.trace_symbol_impact("Distributed Systems", direction="downstream")
        assert impact["total_impacted_locations"] >= 2, f"Expected >= 2 downstream impacts, got {impact['total_impacted_locations']}"

        print("  ✅ SymbolGraphManager Markdown integration passed!")
    finally:
        mgr.clear()
        print("  🧹 Purged test graph cortex_test_markdown_symbols")


def test_smart_chunker_markdown():
    print("🧪 Testing SmartCodeNodeParser hierarchical markdown chunking...")
    parser = SmartCodeNodeParser()
    doc = Document(
        text="""---
title: Knowledge Engine Spec
tags: [knowledge, spec]
---
# Knowledge Engine Spec
Introduction to knowledge graph.

## Graph Topology
Explains node and edge storage.

### Index Optimization
B-tree schema indexing in FalkorDB.
""",
        metadata={"file_path": f"{CORTEX_ROOT}/vault/spec.md"}
    )
    nodes = parser.get_nodes_from_documents([doc])
    assert len(nodes) >= 3, f"Expected at least 3 chunk nodes, got {len(nodes)}"

    scopes = [n.metadata.get("scope") for n in nodes]
    assert any("Knowledge Engine Spec > Graph Topology" in str(s) for s in scopes)
    assert any("Index Optimization" in str(s) for s in scopes)

    for n in nodes:
        assert n.metadata.get("chunk_type") == "markdown_section"
        assert "knowledge" in n.metadata.get("tags", [])
        assert n.text.startswith("# Document:")

    print("  ✅ SmartCodeNodeParser markdown chunking passed!")


if __name__ == "__main__":
    print("\n🚀 Running Cortex Markdown & Knowledge Vault Ingestion Test Suite\n" + "=" * 65)
    test_markdown_extractor()
    test_symbol_graph_markdown_integration()
    test_smart_chunker_markdown()
    print("=" * 65 + "\n🎉 ALL TESTS PASSED SUCCESSFULLY!\n")
