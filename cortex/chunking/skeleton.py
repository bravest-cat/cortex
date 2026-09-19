"""
Architectural file skeleton generation for Cortex AST chunking.
Produces a high-level summary map of imports, top-level definitions, and class member signatures.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from tree_sitter import Node as TSNode

from cortex.chunking.token_utils import count_tokens, extract_first_docstring_text
from cortex.chunking.ast_extractor import get_identifier, get_signature_text, get_body_node


def generate_skeleton_chunk(
    file_path: str,
    module_summary: Optional[str],
    imports: List[str],
    top_defs: List[TSNode],
    source_bytes: bytes,
    max_tokens: int = 512,
    temporal_info: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Generates a concise architectural outline/skeleton chunk for a source file."""
    if not top_defs:
        return None

    lines = [
        f"# File: {file_path}",
        f"# Scope: file_skeleton (Architectural Map)",
    ]
    if module_summary:
        lines.append(f"# Module Summary: {module_summary}")
    if temporal_info and temporal_info.get("commit_date"):
        lines.append(
            f"# Last Modified: {temporal_info['commit_date']} "
            f"({temporal_info['commit_hash']}) - {temporal_info['commit_msg'][:50]}"
        )

    if imports:
        clean_imports = [imp.replace("\n", " ") for imp in imports[:8]]
        lines.append("\nImports:")
        lines.extend(f"- {imp}" for imp in clean_imports)
        if len(imports) > 8:
            lines.append(f"- ... (+{len(imports) - 8} more imports)")

    skeleton_budget = max(max_tokens, 150)
    lines.append("\nDefinitions:")
    added_defs = 0

    for idx, def_node in enumerate(top_defs):
        curr_toks = count_tokens("\n".join(lines))
        if idx > 0 and curr_toks >= skeleton_budget - 20:
            remaining = len(top_defs) - idx
            if remaining > 0:
                lines.append(f"\n... (+{remaining} more definitions)")
            break

        actual = def_node
        if def_node.type == "decorated_definition":
            for c in def_node.children:
                if c.type in ("function_definition", "class_definition", "async_function_definition"):
                    actual = c
                    break

        ident = get_identifier(actual, source_bytes) or "anonymous"
        body_node = get_body_node(actual)
        sig = get_signature_text(actual, body_node, source_bytes)
        doc = extract_first_docstring_text(actual, source_bytes) or (
            extract_first_docstring_text(body_node, source_bytes) if body_node else None
        )
        doc_str = f': "{doc}"' if doc else ""

        if "class" in actual.type or actual.type in (
            "struct_item", "impl_item", "type_declaration",
            "interface_declaration", "trait_item", "enum_item"
        ):
            item_label = "Class"
            if "interface" in actual.type:
                item_label = "Interface"
            elif actual.type == "struct_item":
                item_label = "Struct"
            elif actual.type == "impl_item":
                item_label = "Impl"
            elif actual.type == "trait_item":
                item_label = "Trait"
            elif actual.type == "enum_item":
                item_label = "Enum"
            elif actual.type == "type_declaration":
                item_label = "Type"

            lines.append(f"\n{item_label} `{ident}`:{doc_str}")
            lines.append(f"  Header: {sig}")
            if body_node:
                m_sigs = []
                for child in body_node.children:
                    m_actual = child
                    if child.type == "decorated_definition":
                        for mc in child.children:
                            if mc.type in (
                                "function_definition", "async_function_definition",
                                "method_definition", "function_item", "method_declaration"
                            ):
                                m_actual = mc
                                break
                    if m_actual.type in (
                        "function_definition", "async_function_definition",
                        "method_definition", "function_item", "method_declaration"
                    ):
                        m_body = get_body_node(m_actual)
                        m_sig = get_signature_text(m_actual, m_body, source_bytes)
                        m_sigs.append(m_sig)
                for ms in m_sigs[:8]:
                    lines.append(f"    - {ms}")
                if len(m_sigs) > 8:
                    lines.append(f"    - ... (+{len(m_sigs) - 8} more methods)")
        else:
            lines.append(f"- {sig}{doc_str}")
        added_defs += 1

    skeleton_text = "\n".join(lines).strip()
    return {
        "text": skeleton_text,
        "scope": "file_skeleton",
        "chunk_type": "ast_skeleton",
        "start_char": 0,
        "end_char": len(source_bytes),
        "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
        "commit_date": temporal_info["commit_date"] if temporal_info else "",
        "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
    }
