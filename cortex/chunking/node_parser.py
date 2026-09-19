"""
SmartCodeNodeParser: unified multi-format node parser for Cortex.

Dynamically routes:
- Source code (.py, .ts, .js, .rs, .go, .c, .cpp, .java, .kt, .swift, .cs) -> ASTCodeSplitter
- Markdown (.md, .markdown) -> MarkdownSectionSplitter
- Configuration (.yaml, .yml, .json, .toml) -> ConfigSectionSplitter
- Plain text -> SentenceSplitter
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Sequence

from llama_index.core.node_parser import NodeParser, SentenceSplitter
from llama_index.core.schema import BaseNode, NodeRelationship, RelatedNodeInfo, TextNode

from cortex.chunking.languages import LANG_MAP
from cortex.chunking.config_splitter import ConfigSectionSplitter
from cortex.chunking.markdown_splitter import MarkdownSectionSplitter


class SmartCodeNodeParser(NodeParser):
    """
    State-of-the-Art Intelligent NodeParser for Cortex.
    Dynamically routes files to specialized splitters:
    - Code (.py, .ts, .js, .jsx, .tsx, .rs, .go, .c, .cpp, etc.) -> ASTCodeSplitter
    - Markdown (.md) -> MarkdownSectionSplitter
    - Configs (.yaml, .yml, .json, .toml) -> ConfigSectionSplitter
    - Plain text / Fallbacks -> SentenceSplitter with file breadcrumbs
    """

    chunk_size: int = 2048
    chunk_overlap: int = 128
    max_tokens: int = 512
    overlap_tokens: int = 64

    def __init__(
        self,
        chunk_size: int = 2048,
        chunk_overlap: int = 128,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        **kwargs,
    ):
        super().__init__(chunk_size=chunk_size, chunk_overlap=chunk_overlap, **kwargs)
        # Sizing reconciliation
        if chunk_size != 2048 and max_tokens == 512:
            self.max_tokens = max(128, chunk_size // 4)
        else:
            self.max_tokens = max_tokens

        if chunk_overlap != 128 and overlap_tokens == 64:
            self.overlap_tokens = max(16, chunk_overlap // 4)
        else:
            self.overlap_tokens = overlap_tokens

        self._sentence_splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self._md_splitter = MarkdownSectionSplitter(max_tokens=self.max_tokens)
        self._config_splitter = ConfigSectionSplitter(max_tokens=self.max_tokens)

    def _parse_nodes(
        self,
        nodes: Sequence[BaseNode],
        show_progress: bool = False,
        **kwargs: Any,
    ) -> List[BaseNode]:
        # Lazy import ASTCodeSplitter and get_file_temporal_info to prevent circular imports
        from cortex.chunking.ast_splitter import ASTCodeSplitter, get_file_temporal_info

        all_nodes: List[BaseNode] = []

        for node in nodes:
            file_path = node.metadata.get("file_path", "")
            ext = Path(file_path).suffix.lower() if file_path else ""
            text = node.get_content()
            file_temporal = get_file_temporal_info(file_path) if file_path else None

            # Strategy 1: AST Code Splitter (Code files)
            if ext in LANG_MAP:
                lang = LANG_MAP[ext]
                splitter = ASTCodeSplitter(
                    language=lang,
                    max_tokens=self.max_tokens,
                    overlap_tokens=self.overlap_tokens,
                )
                raw_chunks = splitter.split_code(text, file_path=file_path)

                for chunk in raw_chunks:
                    meta = dict(node.metadata)
                    meta["scope"] = chunk.get("scope", "")
                    meta["chunk_type"] = chunk.get("chunk_type", "ast_code")
                    if "dependencies" in chunk:
                        meta["dependencies"] = chunk["dependencies"]
                    if "parent_id" in chunk:
                        meta["parent_id"] = chunk["parent_id"]
                        meta["parent_scope"] = chunk.get("parent_scope", "")
                        meta["parent_text"] = chunk.get("parent_text", "")
                    if "test_targets" in chunk:
                        meta["test_targets"] = chunk["test_targets"]
                    if "docstring" in chunk:
                        meta["docstring"] = chunk["docstring"]
                    if "signature" in chunk:
                        meta["signature"] = chunk["signature"]
                    if "commit_hash" in chunk:
                        meta["commit_hash"] = chunk["commit_hash"]
                        meta["commit_date"] = chunk.get("commit_date", "")
                        meta["commit_msg"] = chunk.get("commit_msg", "")
                    text_node = TextNode(
                        text=chunk["text"],
                        metadata=meta,
                        start_char_idx=chunk.get("start_char", 0),
                        end_char_idx=chunk.get("end_char", len(chunk["text"])),
                    )
                    text_node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=node.node_id)
                    all_nodes.append(text_node)

            # Strategy 2: Markdown Section Splitter
            elif ext in (".md", ".markdown"):
                raw_chunks = self._md_splitter.split_markdown(text, file_path=file_path)
                for chunk in raw_chunks:
                    meta = dict(node.metadata)
                    meta["scope"] = chunk.get("scope", "")
                    meta["parent_scope"] = chunk.get("parent_scope", "")
                    meta["parent_symbol"] = chunk.get("heading_path", "")
                    meta["heading_path"] = chunk.get("heading_path", "")
                    meta["chunk_type"] = chunk.get("chunk_type", "markdown_section")
                    if chunk.get("tags"):
                        meta["tags"] = chunk["tags"]
                    if file_temporal:
                        meta["commit_hash"] = file_temporal["commit_hash"]
                        meta["commit_date"] = file_temporal["commit_date"]
                        meta["commit_msg"] = file_temporal["commit_msg"]
                    text_node = TextNode(
                        text=chunk["text"],
                        metadata=meta,
                        start_char_idx=chunk.get("start_char", 0),
                        end_char_idx=chunk.get("end_char", len(chunk["text"])),
                    )
                    text_node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=node.node_id)
                    all_nodes.append(text_node)

            # Strategy 3: Structured Config Splitter (.json, .yaml, .yml, .toml)
            elif ext in (".json", ".yaml", ".yml", ".toml"):
                raw_chunks = self._config_splitter.split_config(text, file_path=file_path)
                for chunk in raw_chunks:
                    meta = dict(node.metadata)
                    meta["scope"] = chunk.get("scope", "")
                    meta["chunk_type"] = chunk.get("chunk_type", "config_section")
                    if file_temporal:
                        meta["commit_hash"] = file_temporal["commit_hash"]
                        meta["commit_date"] = file_temporal["commit_date"]
                        meta["commit_msg"] = file_temporal["commit_msg"]
                    text_node = TextNode(
                        text=chunk["text"],
                        metadata=meta,
                        start_char_idx=chunk.get("start_char", 0),
                        end_char_idx=chunk.get("end_char", len(chunk["text"])),
                    )
                    text_node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=node.node_id)
                    all_nodes.append(text_node)

            # Strategy 4: Fallback Plain Text Sentence Splitter
            else:
                sub_nodes = self._sentence_splitter.get_nodes_from_documents([node])
                for sn in sub_nodes:
                    meta = dict(sn.metadata)
                    meta["chunk_type"] = "text_sentence"
                    if file_temporal:
                        meta["commit_hash"] = file_temporal["commit_hash"]
                        meta["commit_date"] = file_temporal["commit_date"]
                        meta["commit_msg"] = file_temporal["commit_msg"]
                    breadcrumb = f"# File: {file_path}\n\n" if file_path else ""
                    sn.text = breadcrumb + sn.text
                    sn.metadata = meta
                    all_nodes.append(sn)

        return all_nodes
