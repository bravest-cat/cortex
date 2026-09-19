"""
AST-Aware Hierarchical Code Chunker for Cortex.
Skeletons, statement sliding-window overlap, and tiny-stub coalescing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from cortex.chunking.languages import (
    Language,
    Parser,
    TSNode,
    LANG_MAP,
    PY_LANG,
    JS_LANG,
    TS_LANG,
    TSX_LANG,
    RUST_LANG,
    GO_LANG,
    print_language_status,
    SUPPORTED_CODE_EXTS,
    ALL_SUPPORTED_EXTS,
)
from cortex.chunking.token_utils import (
    count_tokens,
    get_file_temporal_info,
    extract_first_docstring_text,
)
from cortex.chunking.ast_extractor import (
    get_identifier,
    get_signature_text,
    get_body_node,
    extract_symbols_and_references,
)
from cortex.chunking.skeleton import generate_skeleton_chunk


class ASTCodeSplitter:
    """
    Advanced AST-aware code splitter with token precision, contextual preambles,
    tiny-stub coalescing, statement sliding window overlap, and file skeleton generation.
    """

    def __init__(
        self,
        language: Language,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        stub_threshold_tokens: int = 100,
        coalesce_target_tokens: int = 384,
        max_chunk_size: Optional[int] = None,
        overlap: Optional[int] = None,
    ):
        self.language = language
        # Backward compatibility with character counts
        if max_chunk_size is not None and max_tokens == 512:
            self.max_tokens = max(128, max_chunk_size // 4)
        else:
            self.max_tokens = max_tokens

        if overlap is not None and overlap_tokens == 64:
            self.overlap_tokens = max(16, overlap // 4)
        else:
            self.overlap_tokens = overlap_tokens

        self.stub_threshold_tokens = stub_threshold_tokens
        self.coalesce_target_tokens = coalesce_target_tokens
        self.parser = Parser(language)

    _get_identifier = staticmethod(get_identifier)
    _get_signature_text = staticmethod(get_signature_text)
    _get_body_node = staticmethod(get_body_node)

    def _make_context_preamble(
        self,
        file_path: str,
        module_summary: Optional[str] = None,
        scope: str = "",
        signature: Optional[str] = None,
        part_info: Optional[str] = None,
        docstring: Optional[str] = None,
        dependencies: Optional[List[Dict[str, str]]] = None,
        test_targets: Optional[List[str]] = None,
        temporal_info: Optional[Dict[str, str]] = None,
    ) -> str:
        lines = [f"# File: {file_path}"]
        if module_summary:
            lines.append(f"# Module: {module_summary}")
        if scope:
            lines.append(f"# Scope: {scope}")
        if signature:
            lines.append(f"# Signature: {signature}")
        if docstring:
            clean_doc = docstring.replace("\n", " ").strip()
            lines.append(f"# Docstring: {clean_doc}")
        if dependencies:
            dep_strs = [f"{d['symbol']} ({d['module']})" if d.get("module") else d["symbol"] for d in dependencies[:6]]
            lines.append(f"# Depends On: {', '.join(dep_strs)}")
        if test_targets:
            lines.append(f"# Tests Target: {', '.join(test_targets[:4])}")
        if temporal_info and temporal_info.get("commit_date"):
            lines.append(f"# Last Modified: {temporal_info['commit_date']} ({temporal_info['commit_hash']}) - {temporal_info['commit_msg'][:50]}")
        if part_info:
            lines.append(f"# Note: {part_info}")
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _resolve_deps(text: str, import_map: Dict[str, str], self_ident: Optional[str] = None) -> List[Dict[str, str]]:
        used = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text))
        return [
            {"symbol": s, "module": import_map[s]}
            for s in sorted(used)
            if s in import_map and s != self_ident
        ]

    @staticmethod
    def _resolve_test_targets(
        ident: str,
        text: str,
        file_path: str,
        import_map: Dict[str, str],
        declarations: List[Dict[str, Any]],
    ) -> List[str]:
        is_test = (
            ident.startswith("test_")
            or ident.endswith("_test")
            or ident.startswith("Test")
            or "test" in Path(file_path).parts
            or "tests" in Path(file_path).parts
        )
        if not is_test:
            return []
        called = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text))
        targets = []
        for name in sorted(called):
            if name in import_map or any(d.get("name") == name for d in declarations):
                if not name.startswith("test_") and name not in (
                    "assert", "assertTrue", "assertEqual", "pytest", "print", "len",
                    "range", "list", "dict", "set", "int", "str", "float", "bool", "super",
                ):
                    targets.append(name)
        return targets[:4]

    def extract_symbols_and_references(self, source_code: str, file_path: str = "") -> Dict[str, Any]:
        """
        Extracts syntactic declarations, imports, and symbol references for FalkorDB graph
        across Python, TypeScript, JavaScript, Rust, Go, C, C++, Java, Kotlin, Swift, and C#.
        """
        return extract_symbols_and_references(self.language, source_code, file_path=file_path)

    def split_code(self, source_code: str, file_path: str = "") -> List[Dict[str, Any]]:
        """
        Parses source code into token-sized, contextualized AST chunks.
        Also produces a 1-per-file skeleton chunk.
        Enriched with dependency injection, test target linkage, docstrings, and temporal git metadata.
        """
        source_bytes = source_code.encode("utf-8")
        tree = self.parser.parse(source_bytes)
        root = tree.root_node

        chunks: List[Dict[str, Any]] = []

        # 1. Extract temporal metadata and symbols/imports for dependency linking
        temporal_info = get_file_temporal_info(file_path)
        ast_info = self.extract_symbols_and_references(source_code, file_path=file_path)
        import_map: Dict[str, str] = {
            imp["symbol"]: imp.get("module", "")
            for imp in ast_info.get("imports", [])
            if imp.get("symbol")
        }
        decls = ast_info.get("declarations", [])
        _get_deps = lambda txt, s_id=None: self._resolve_deps(txt, import_map, s_id)
        _get_test_targets = lambda idn, txt: self._resolve_test_targets(idn, txt, file_path, import_map, decls)

        # 2. Extract module docstring
        module_summary = extract_first_docstring_text(root, source_bytes)

        # 3. Collect imports, top-level definitions, and setup code
        imports_list: List[str] = []
        top_defs: List[TSNode] = []
        preamble_nodes: List[TSNode] = []

        for child in root.children:
            actual = child
            if child.type == "decorated_definition":
                for c in child.children:
                    if c.type in (
                        "function_definition", "class_definition", "async_function_definition",
                        "function_declaration", "class_declaration", "export_statement"
                    ):
                        actual = c
                        break

            if actual.type in (
                "function_definition", "class_definition", "async_function_definition",
                "function_declaration", "class_declaration", "export_statement",
                "struct_item", "enum_item", "trait_item", "impl_item", "function_item",
                "type_declaration", "method_declaration",
                "class_specifier", "struct_specifier", "namespace_definition", "namespace_declaration",
                "interface_declaration", "record_declaration", "object_declaration", "protocol_declaration",
                "constructor_declaration", "initializer_declaration",
            ):
                top_defs.append(child)
            elif "import" in child.type or "export" in child.type or child.type in ("use_declaration", "preproc_include", "using_directive", "import_header"):
                imp_text = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace").strip()
                imports_list.append(imp_text)
                if not top_defs:
                    preamble_nodes.append(child)
            else:
                if not top_defs:
                    preamble_nodes.append(child)

        # 4. Generate File Skeleton / Map Chunk
        skeleton_chunk = self._generate_skeleton_chunk(
            file_path=file_path,
            module_summary=module_summary,
            imports=imports_list,
            top_defs=top_defs,
            source_bytes=source_bytes,
            temporal_info=temporal_info,
        )
        if skeleton_chunk:
            chunks.append(skeleton_chunk)

        # 5. Process Top-Level Preamble (Imports & Setup)
        if preamble_nodes:
            start_b = preamble_nodes[0].start_byte
            end_b = preamble_nodes[-1].end_byte
            preamble_text = source_bytes[start_b:end_b].decode("utf-8", errors="replace").strip()
            if count_tokens(preamble_text) >= 20:
                header = self._make_context_preamble(
                    file_path,
                    module_summary,
                    "module imports & setup",
                    temporal_info=temporal_info,
                )
                chunks.append({
                    "text": header + preamble_text,
                    "scope": "module setup",
                    "chunk_type": "ast_code",
                    "start_char": start_b,
                    "end_char": end_b,
                    "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
                    "commit_date": temporal_info["commit_date"] if temporal_info else "",
                    "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
                })

        # 6. Process Classes and Functions with Coalescing and Statement Overlap
        buffered_stubs: List[Tuple[str, str, int, int]] = []  # (text, scope, start_b, end_b)
        buffered_tokens = 0

        def flush_stubs():
            nonlocal buffered_stubs, buffered_tokens
            if not buffered_stubs:
                return
            combined_text = "\n\n".join(item[0] for item in buffered_stubs)
            scopes = [item[1] for item in buffered_stubs]
            coalesced_scope = f"coalesced stubs: {', '.join(scopes[:4])}"
            if len(scopes) > 4:
                coalesced_scope += f" (+{len(scopes) - 4} more)"

            stub_deps = _get_deps(combined_text)
            stub_targets: List[str] = []
            for s_name in scopes:
                stub_targets.extend(_get_test_targets(s_name, combined_text))

            header = self._make_context_preamble(
                file_path=file_path,
                module_summary=module_summary,
                scope=coalesced_scope,
                part_info=f"Coalesced {len(buffered_stubs)} adjacent definitions",
                dependencies=stub_deps,
                test_targets=stub_targets[:4],
                temporal_info=temporal_info,
            )
            chunks.append({
                "text": header + combined_text,
                "scope": coalesced_scope,
                "chunk_type": "ast_code",
                "start_char": buffered_stubs[0][2],
                "end_char": buffered_stubs[-1][3],
                "dependencies": stub_deps,
                "test_targets": stub_targets[:4],
                "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
                "commit_date": temporal_info["commit_date"] if temporal_info else "",
                "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
            })
            buffered_stubs = []
            buffered_tokens = 0

        for node in top_defs:
            actual = node
            if node.type == "decorated_definition":
                for c in node.children:
                    if c.type in ("function_definition", "class_definition", "async_function_definition"):
                        actual = c
                        break

            node_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            node_toks = count_tokens(node_text)
            ident = self._get_identifier(actual, source_bytes) or "anonymous"

            if "class" in actual.type or actual.type in ("struct_item", "impl_item", "type_declaration", "interface_declaration"):
                # Flush any buffered stubs before processing a class/struct/impl
                flush_stubs()
                class_chunks = self._split_class(
                    outer_node=node,
                    class_node=actual,
                    class_name=ident,
                    source_bytes=source_bytes,
                    file_path=file_path,
                    module_summary=module_summary,
                    import_map=import_map,
                    ast_info=ast_info,
                    temporal_info=temporal_info,
                )
                chunks.extend(class_chunks)
            else:
                # Top-level function
                body_node = self._get_body_node(actual)
                sig = self._get_signature_text(actual, body_node, source_bytes)
                fn_doc = extract_first_docstring_text(actual, source_bytes) or (extract_first_docstring_text(body_node, source_bytes) if body_node else None)
                scope_str = f"def {ident}"
                deps = _get_deps(node_text, ident)
                targets = _get_test_targets(ident, node_text)

                # Tiny-stub coalescing for top-level small helpers
                if node_toks < self.stub_threshold_tokens:
                    if buffered_tokens + node_toks > self.coalesce_target_tokens and buffered_stubs:
                        flush_stubs()
                    buffered_stubs.append((node_text, ident, node.start_byte, node.end_byte))
                    buffered_tokens += node_toks
                else:
                    flush_stubs()
                    if node_toks <= self.max_tokens:
                        header = self._make_context_preamble(
                            file_path=file_path,
                            module_summary=module_summary,
                            scope=scope_str,
                            signature=sig,
                            docstring=fn_doc,
                            dependencies=deps,
                            test_targets=targets,
                            temporal_info=temporal_info,
                        )
                        chunks.append({
                            "text": header + node_text,
                            "scope": scope_str,
                            "chunk_type": "ast_code",
                            "start_char": node.start_byte,
                            "end_char": node.end_byte,
                            "signature": sig,
                            "docstring": fn_doc or "",
                            "dependencies": deps,
                            "test_targets": targets,
                            "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
                            "commit_date": temporal_info["commit_date"] if temporal_info else "",
                            "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
                        })
                    else:
                        # AST Statement-aware sliding window split
                        sub_chunks = self._split_oversized_function(
                            node=node,
                            actual_fn=actual,
                            scope=scope_str,
                            signature=sig,
                            fn_doc=fn_doc,
                            source_bytes=source_bytes,
                            file_path=file_path,
                            module_summary=module_summary,
                            import_map=import_map,
                            ast_info=ast_info,
                            temporal_info=temporal_info,
                        )
                        chunks.extend(sub_chunks)

        flush_stubs()

        # Fallback if no chunks generated
        if not chunks:
            header = self._make_context_preamble(
                file_path=file_path,
                module_summary=module_summary,
                scope="module",
                temporal_info=temporal_info,
            )
            sub_chunks = self._subsplit_text_with_overlap(source_code, header, "module", 0)
            for sc in sub_chunks:
                sc["commit_hash"] = temporal_info["commit_hash"] if temporal_info else "local"
                sc["commit_date"] = temporal_info["commit_date"] if temporal_info else ""
                sc["commit_msg"] = temporal_info["commit_msg"] if temporal_info else ""
            chunks.extend(sub_chunks)

        return chunks

    def _generate_skeleton_chunk(
        self,
        file_path: str,
        module_summary: Optional[str],
        imports: List[str],
        top_defs: List[TSNode],
        source_bytes: bytes,
        temporal_info: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generates a concise architectural outline/skeleton chunk for the file."""
        return generate_skeleton_chunk(
            file_path=file_path,
            module_summary=module_summary,
            imports=imports,
            top_defs=top_defs,
            source_bytes=source_bytes,
            max_tokens=self.max_tokens,
            temporal_info=temporal_info,
        )

    def _split_class(
        self,
        outer_node: TSNode,
        class_node: TSNode,
        class_name: str,
        source_bytes: bytes,
        file_path: str,
        module_summary: Optional[str],
        import_map: Optional[Dict[str, str]] = None,
        ast_info: Optional[Dict[str, Any]] = None,
        temporal_info: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Splits class methods while coalescing tiny stubs and maintaining parent-child hierarchy."""
        import_map = import_map or {}
        ast_info = ast_info or {}
        full_text = source_bytes[outer_node.start_byte:outer_node.end_byte].decode("utf-8", errors="replace")
        total_tokens = count_tokens(full_text)

        body_node = self._get_body_node(class_node)
        class_doc = extract_first_docstring_text(class_node, source_bytes) or (extract_first_docstring_text(body_node, source_bytes) if body_node else None)
        class_sig = self._get_signature_text(class_node, body_node, source_bytes)

        parent_id = f"{file_path}::class::{class_name}"
        parent_scope = f"class {class_name}"
        parent_text = f"{class_sig}\n{class_doc or ''}"[:2000]

        decls = ast_info.get("declarations", [])
        _get_deps = lambda txt, s_id=None: self._resolve_deps(txt, import_map, s_id)
        _get_test_targets = lambda idn, txt: self._resolve_test_targets(idn, txt, file_path, import_map, decls)

        # If entire class fits within max_tokens, keep intact
        if total_tokens <= self.max_tokens:
            deps = _get_deps(full_text, class_name)
            targets = _get_test_targets(class_name, full_text)
            header = self._make_context_preamble(
                file_path=file_path,
                module_summary=module_summary,
                scope=f"class {class_name}",
                signature=class_sig,
                docstring=class_doc,
                dependencies=deps,
                test_targets=targets,
                temporal_info=temporal_info,
            )
            return [{
                "text": header + full_text,
                "scope": f"class {class_name}",
                "chunk_type": "ast_code",
                "start_char": outer_node.start_byte,
                "end_char": outer_node.end_byte,
                "signature": class_sig,
                "docstring": class_doc or "",
                "dependencies": deps,
                "test_targets": targets,
                "parent_id": parent_id,
                "parent_scope": parent_scope,
                "parent_text": parent_text,
                "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
                "commit_date": temporal_info["commit_date"] if temporal_info else "",
                "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
            }]

        chunks: List[Dict[str, Any]] = []

        # Extract methods
        methods: List[TSNode] = []
        if body_node:
            for child in body_node.children:
                actual = child
                if child.type == "decorated_definition":
                    for c in child.children:
                        if c.type in ("function_definition", "async_function_definition", "method_definition", "function_item", "method_declaration"):
                            actual = c
                            break
                if actual.type in ("function_definition", "async_function_definition", "method_definition", "function_item", "method_declaration"):
                    methods.append(child)

        # Tiny-stub coalescing within class
        buffered_methods: List[Tuple[str, str, int, int]] = []
        buffered_tokens = 0

        def flush_method_stubs():
            nonlocal buffered_methods, buffered_tokens
            if not buffered_methods:
                return
            combined_text = "\n\n".join(item[0] for item in buffered_methods)
            names = [item[1] for item in buffered_methods]
            coalesced_scope = f"class {class_name} > methods: {', '.join(names[:4])}"
            if len(names) > 4:
                coalesced_scope += f" (+{len(names) - 4} more)"

            stub_deps = _get_deps(combined_text)
            stub_targets: List[str] = []
            for m_name in names:
                stub_targets.extend(_get_test_targets(m_name, combined_text))

            header = self._make_context_preamble(
                file_path=file_path,
                module_summary=module_summary,
                scope=coalesced_scope,
                signature=f"class {class_name}: {class_doc or ''}".strip(),
                part_info=f"Coalesced {len(buffered_methods)} short methods",
                dependencies=stub_deps,
                test_targets=stub_targets[:4],
                temporal_info=temporal_info,
            )
            chunks.append({
                "text": header + combined_text,
                "scope": coalesced_scope,
                "chunk_type": "ast_code",
                "start_char": buffered_methods[0][2],
                "end_char": buffered_methods[-1][3],
                "dependencies": stub_deps,
                "test_targets": stub_targets[:4],
                "parent_id": parent_id,
                "parent_scope": parent_scope,
                "parent_text": parent_text,
                "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
                "commit_date": temporal_info["commit_date"] if temporal_info else "",
                "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
            })
            buffered_methods = []
            buffered_tokens = 0

        for m in methods:
            actual = m
            if m.type == "decorated_definition":
                for c in m.children:
                    if c.type in ("function_definition", "async_function_definition", "method_definition", "function_item", "method_declaration"):
                        actual = c
                        break

            m_text = source_bytes[m.start_byte:m.end_byte].decode("utf-8", errors="replace")
            m_tokens = count_tokens(m_text)
            m_name = self._get_identifier(actual, source_bytes) or "method"
            m_body = self._get_body_node(actual)
            m_sig = self._get_signature_text(actual, m_body, source_bytes)
            m_doc = extract_first_docstring_text(actual, source_bytes) or (extract_first_docstring_text(m_body, source_bytes) if m_body else None)
            scope_str = f"class {class_name} > def {m_name}"
            m_deps = _get_deps(m_text, m_name)
            m_targets = _get_test_targets(m_name, m_text)

            # Check for tiny-stub coalescing
            if m_tokens < self.stub_threshold_tokens:
                if buffered_tokens + m_tokens > self.coalesce_target_tokens and buffered_methods:
                    flush_method_stubs()
                buffered_methods.append((m_text, m_name, m.start_byte, m.end_byte))
                buffered_tokens += m_tokens
            else:
                flush_method_stubs()
                if m_tokens <= self.max_tokens:
                    header = self._make_context_preamble(
                        file_path=file_path,
                        module_summary=module_summary,
                        scope=scope_str,
                        signature=f"{class_sig} -> {m_sig}",
                        docstring=m_doc,
                        dependencies=m_deps,
                        test_targets=m_targets,
                        temporal_info=temporal_info,
                    )
                    chunks.append({
                        "text": header + m_text,
                        "scope": scope_str,
                        "chunk_type": "ast_code",
                        "start_char": m.start_byte,
                        "end_char": m.end_byte,
                        "signature": f"{class_sig} -> {m_sig}",
                        "docstring": m_doc or "",
                        "dependencies": m_deps,
                        "test_targets": m_targets,
                        "parent_id": parent_id,
                        "parent_scope": parent_scope,
                        "parent_text": parent_text,
                        "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
                        "commit_date": temporal_info["commit_date"] if temporal_info else "",
                        "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
                    })
                else:
                    # Oversized method: AST Statement-aware splitting
                    sub_chunks = self._split_oversized_function(
                        node=m,
                        actual_fn=actual,
                        scope=scope_str,
                        signature=f"{class_sig} -> {m_sig}",
                        fn_doc=m_doc,
                        source_bytes=source_bytes,
                        file_path=file_path,
                        module_summary=module_summary,
                        import_map=import_map,
                        ast_info=ast_info,
                        temporal_info=temporal_info,
                        parent_id=parent_id,
                        parent_scope=parent_scope,
                        parent_text=parent_text,
                    )
                    chunks.extend(sub_chunks)

        flush_method_stubs()

        # Fallback if no methods extracted
        if not chunks:
            deps = _get_deps(full_text, class_name)
            targets = _get_test_targets(class_name, full_text)
            header = self._make_context_preamble(
                file_path=file_path,
                module_summary=module_summary,
                scope=f"class {class_name}",
                signature=class_sig,
                docstring=class_doc,
                dependencies=deps,
                test_targets=targets,
                temporal_info=temporal_info,
            )
            sub_chunks = self._subsplit_text_with_overlap(full_text, header, f"class {class_name}", outer_node.start_byte)
            for sc in sub_chunks:
                sc["parent_id"] = parent_id
                sc["parent_scope"] = parent_scope
                sc["parent_text"] = parent_text
                sc["commit_hash"] = temporal_info["commit_hash"] if temporal_info else "local"
                sc["commit_date"] = temporal_info["commit_date"] if temporal_info else ""
                sc["commit_msg"] = temporal_info["commit_msg"] if temporal_info else ""
            chunks.extend(sub_chunks)

        return chunks

    def _split_oversized_function(
        self,
        node: TSNode,
        actual_fn: TSNode,
        scope: str,
        signature: str,
        fn_doc: Optional[str],
        source_bytes: bytes,
        file_path: str,
        module_summary: Optional[str],
        import_map: Optional[Dict[str, str]] = None,
        ast_info: Optional[Dict[str, Any]] = None,
        temporal_info: Optional[Dict[str, str]] = None,
        parent_id: Optional[str] = None,
        parent_scope: Optional[str] = None,
        parent_text: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Splits a large function along AST statement boundaries with sliding-window overlap.
        Preserves complete control flow (try/except, loops, conditions) and parent hierarchy.
        """
        import_map = import_map or {}
        ast_info = ast_info or {}
        if parent_id is None:
            clean_scope = re.sub(r"[^A-Za-z0-9_]+", "_", scope).strip("_")
            parent_id = f"{file_path}::{clean_scope}"
            parent_scope = scope
            parent_text = f"{signature}\n{fn_doc or ''}"[:2000]

        decls = ast_info.get("declarations", [])
        _get_deps = lambda txt: self._resolve_deps(txt, import_map)
        _get_test_targets = lambda idn, txt: self._resolve_test_targets(idn, txt, file_path, import_map, decls)

        body_node = self._get_body_node(actual_fn)
        if not body_node or not body_node.children:
            full_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            header = self._make_context_preamble(
                file_path, module_summary, scope, signature, docstring=fn_doc, temporal_info=temporal_info
            )
            sub_chunks = self._subsplit_text_with_overlap(full_text, header, scope, node.start_byte)
            for sc in sub_chunks:
                sc["parent_id"] = parent_id
                sc["parent_scope"] = parent_scope
                sc["parent_text"] = parent_text
                sc["commit_hash"] = temporal_info["commit_hash"] if temporal_info else "local"
                sc["commit_date"] = temporal_info["commit_date"] if temporal_info else ""
                sc["commit_msg"] = temporal_info["commit_msg"] if temporal_info else ""
            return sub_chunks

        # Filter meaningful statements (ignore braces, colons, docstrings if already extracted)
        statements: List[TSNode] = []
        for child in body_node.children:
            if child.type in (":", "{", "}"):
                continue
            statements.append(child)

        if not statements:
            full_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            header = self._make_context_preamble(
                file_path, module_summary, scope, signature, docstring=fn_doc, temporal_info=temporal_info
            )
            sub_chunks = self._subsplit_text_with_overlap(full_text, header, scope, node.start_byte)
            for sc in sub_chunks:
                sc["parent_id"] = parent_id
                sc["parent_scope"] = parent_scope
                sc["parent_text"] = parent_text
                sc["commit_hash"] = temporal_info["commit_hash"] if temporal_info else "local"
                sc["commit_date"] = temporal_info["commit_date"] if temporal_info else ""
                sc["commit_msg"] = temporal_info["commit_msg"] if temporal_info else ""
            return sub_chunks

        chunks: List[Dict[str, Any]] = []
        curr_stmts: List[TSNode] = []
        curr_tokens = 0
        part_idx = 1

        for stmt in statements:
            stmt_text = source_bytes[stmt.start_byte:stmt.end_byte].decode("utf-8", errors="replace")
            stmt_toks = count_tokens(stmt_text)

            # If a single statement exceeds max_tokens, sub-split it with text overlap
            if stmt_toks > self.max_tokens:
                if curr_stmts:
                    c_text = source_bytes[curr_stmts[0].start_byte:curr_stmts[-1].end_byte].decode("utf-8", errors="replace")
                    c_deps = _get_deps(c_text)
                    c_targets = _get_test_targets(scope, c_text)
                    chunks.append(self._build_statement_chunk(
                        curr_stmts, source_bytes, file_path, module_summary, scope, signature, part_idx,
                        docstring=fn_doc, dependencies=c_deps, test_targets=c_targets, temporal_info=temporal_info,
                        parent_id=parent_id, parent_scope=parent_scope, parent_text=parent_text,
                    ))
                    part_idx += 1
                    curr_stmts = []
                    curr_tokens = 0

                s_deps = _get_deps(stmt_text)
                s_targets = _get_test_targets(scope, stmt_text)
                sub_hdr = self._make_context_preamble(
                    file_path, module_summary, scope, signature, part_info=f"Part {part_idx} (large statement)",
                    docstring=fn_doc, dependencies=s_deps, test_targets=s_targets, temporal_info=temporal_info,
                )
                stmt_chunks = self._subsplit_text_with_overlap(stmt_text, sub_hdr, scope, stmt.start_byte)
                for sc in stmt_chunks:
                    sc["parent_id"] = parent_id
                    sc["parent_scope"] = parent_scope
                    sc["parent_text"] = parent_text
                    sc["dependencies"] = s_deps
                    sc["test_targets"] = s_targets
                    sc["commit_hash"] = temporal_info["commit_hash"] if temporal_info else "local"
                    sc["commit_date"] = temporal_info["commit_date"] if temporal_info else ""
                    sc["commit_msg"] = temporal_info["commit_msg"] if temporal_info else ""
                chunks.extend(stmt_chunks)
                part_idx += len(stmt_chunks)
                continue

            if curr_tokens + stmt_toks > self.max_tokens and curr_stmts:
                c_text = source_bytes[curr_stmts[0].start_byte:curr_stmts[-1].end_byte].decode("utf-8", errors="replace")
                c_deps = _get_deps(c_text)
                c_targets = _get_test_targets(scope, c_text)
                chunks.append(self._build_statement_chunk(
                    curr_stmts, source_bytes, file_path, module_summary, scope, signature, part_idx,
                    docstring=fn_doc, dependencies=c_deps, test_targets=c_targets, temporal_info=temporal_info,
                    parent_id=parent_id, parent_scope=parent_scope, parent_text=parent_text,
                ))
                part_idx += 1

                # Calculate AST sliding-window overlap: retain last N statements <= overlap_tokens
                overlap_stmts: List[TSNode] = []
                overlap_accum = 0
                for prev in reversed(curr_stmts):
                    prev_text = source_bytes[prev.start_byte:prev.end_byte].decode("utf-8", errors="replace")
                    prev_toks = count_tokens(prev_text)
                    if overlap_accum + prev_toks <= self.overlap_tokens:
                        overlap_stmts.insert(0, prev)
                        overlap_accum += prev_toks
                    else:
                        break

                curr_stmts = list(overlap_stmts)
                curr_tokens = overlap_accum

            curr_stmts.append(stmt)
            curr_tokens += stmt_toks

        if curr_stmts:
            c_text = source_bytes[curr_stmts[0].start_byte:curr_stmts[-1].end_byte].decode("utf-8", errors="replace")
            c_deps = _get_deps(c_text)
            c_targets = _get_test_targets(scope, c_text)
            chunks.append(self._build_statement_chunk(
                curr_stmts, source_bytes, file_path, module_summary, scope, signature, part_idx,
                docstring=fn_doc, dependencies=c_deps, test_targets=c_targets, temporal_info=temporal_info,
                parent_id=parent_id, parent_scope=parent_scope, parent_text=parent_text,
            ))

        return chunks

    def _build_statement_chunk(
        self,
        stmts: List[TSNode],
        source_bytes: bytes,
        file_path: str,
        module_summary: Optional[str],
        scope: str,
        signature: str,
        part_idx: int,
        docstring: Optional[str] = None,
        dependencies: Optional[List[Dict[str, str]]] = None,
        test_targets: Optional[List[str]] = None,
        temporal_info: Optional[Dict[str, str]] = None,
        parent_id: Optional[str] = None,
        parent_scope: Optional[str] = None,
        parent_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        start_b = stmts[0].start_byte
        end_b = stmts[-1].end_byte
        body_text = source_bytes[start_b:end_b].decode("utf-8", errors="replace").strip()
        part_info = f"Part {part_idx}" if part_idx > 1 else None
        header = self._make_context_preamble(
            file_path=file_path,
            module_summary=module_summary,
            scope=scope,
            signature=signature,
            part_info=part_info,
            docstring=docstring,
            dependencies=dependencies,
            test_targets=test_targets,
            temporal_info=temporal_info,
        )
        return {
            "text": header + body_text,
            "scope": f"{scope} ({part_info or 'full'})",
            "chunk_type": "ast_code",
            "start_char": start_b,
            "end_char": end_b,
            "signature": signature,
            "docstring": docstring or "",
            "dependencies": dependencies or [],
            "test_targets": test_targets or [],
            "parent_id": parent_id,
            "parent_scope": parent_scope,
            "parent_text": parent_text,
            "commit_hash": temporal_info["commit_hash"] if temporal_info else "local",
            "commit_date": temporal_info["commit_date"] if temporal_info else "",
            "commit_msg": temporal_info["commit_msg"] if temporal_info else "",
        }

    def _subsplit_text_with_overlap(
        self,
        text: str,
        header: str,
        scope: str,
        base_offset: int,
    ) -> List[Dict[str, Any]]:
        """Fallback line-based sub-splitter with token overlap."""
        lines = text.splitlines(keepends=True)
        chunks = []
        curr_lines = []
        curr_tokens = count_tokens(header)
        curr_start = base_offset

        for line in lines:
            line_toks = count_tokens(line)
            if curr_tokens + line_toks > self.max_tokens and curr_lines:
                chunk_body = "".join(curr_lines).strip()
                chunks.append({
                    "text": header + chunk_body,
                    "scope": scope,
                    "chunk_type": "ast_code",
                    "start_char": curr_start,
                    "end_char": curr_start + len(chunk_body),
                })
                # Overlap last ~15% lines
                overlap_lines = curr_lines[-3:] if len(curr_lines) >= 3 else []
                curr_lines = list(overlap_lines)
                curr_tokens = count_tokens(header) + sum(count_tokens(l) for l in curr_lines)
                curr_start += sum(len(l) for l in curr_lines[:-3]) if len(curr_lines) > 3 else 0

            curr_lines.append(line)
            curr_tokens += line_toks

        if curr_lines:
            chunk_body = "".join(curr_lines).strip()
            chunks.append({
                "text": header + chunk_body,
                "scope": scope,
                "chunk_type": "ast_code",
                "start_char": curr_start,
                "end_char": curr_start + len(chunk_body),
            })

        return chunks


from cortex.chunking.skeleton import generate_skeleton_chunk

__all__ = [
    "ASTCodeSplitter", "ConfigSectionSplitter", "MarkdownSectionSplitter",
    "SmartCodeNodeParser", "generate_skeleton_chunk", "count_tokens",
    "get_file_temporal_info", "extract_first_docstring_text", "LANG_MAP",
    "PY_LANG", "JS_LANG", "TS_LANG", "TSX_LANG", "RUST_LANG", "GO_LANG",
    "print_language_status", "SUPPORTED_CODE_EXTS", "ALL_SUPPORTED_EXTS",
]
