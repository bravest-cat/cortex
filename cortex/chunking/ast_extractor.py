"""
AST Symbol, Import, and Reference Extractor for Cortex.

Extracts syntactic declarations, imports, and cross-file symbol references
across Python, TypeScript, JavaScript, Rust, Go, C, C++, Java, Kotlin, Swift, and C#.
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Tuple

try:
    from tree_sitter import Language, Node as TSNode, Parser
except ImportError:
    Language = Any
    TSNode = Any
    Parser = Any

from cortex.chunking.token_utils import extract_first_docstring_text


def get_identifier(node: TSNode, source_bytes: bytes) -> Optional[str]:
    """Extracts the identifier name from a Tree-sitter node across languages."""
    if node is None:
        return None
    name_node = node.child_by_field_name("name")
    if name_node:
        return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace").strip()
    decl_node = node.child_by_field_name("declarator")
    if decl_node:
        res = get_identifier(decl_node, source_bytes)
        if res:
            return res
    type_node = node.child_by_field_name("type")
    if type_node and node.type in ("impl_item", "type_spec"):
        return source_bytes[type_node.start_byte:type_node.end_byte].decode("utf-8", errors="replace").strip()
    if node.type == "type_declaration":
        for c in node.children:
            if c.type == "type_spec":
                res = get_identifier(c, source_bytes)
                if res:
                    return res
    if node.type in ("identifier", "name", "simple_identifier", "type_identifier", "property_identifier", "field_identifier", "package_identifier"):
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
    for c in node.children:
        if c.type in ("identifier", "name", "simple_identifier", "type_identifier", "property_identifier", "field_identifier", "package_identifier"):
            return source_bytes[c.start_byte:c.end_byte].decode("utf-8", errors="replace").strip()
    return None


def get_signature_text(def_node: TSNode, body_node: Optional[TSNode], source_bytes: bytes) -> str:
    """Extracts the signature up to the body block."""
    if body_node:
        sig_bytes = source_bytes[def_node.start_byte:body_node.start_byte].strip()
    else:
        raw = source_bytes[def_node.start_byte:def_node.end_byte].decode("utf-8", errors="replace")
        sig_bytes = raw.splitlines()[0].encode("utf-8")
    sig_str = sig_bytes.decode("utf-8", errors="replace").strip()
    if sig_str.endswith("{") or sig_str.endswith(":"):
        sig_str = sig_str[:-1].strip()
    return sig_str


def get_body_node(node: TSNode) -> Optional[TSNode]:
    """Finds the body or block node of a function, class, struct, or impl."""
    if node is None:
        return None
    body_field = node.child_by_field_name("body")
    if body_field:
        return body_field
    for c in node.children:
        if c.type in ("block", "statement_block", "class_body", "declaration_list", "field_declaration_list", "compound_statement", "function_body", "statements"):
            return c
    return None


def extract_symbols_and_references(
    language: Language,
    source_code: str,
    file_path: str = "",
) -> Dict[str, Any]:
    """
    Extracts syntactic declarations, imports, and symbol references for FalkorDB graph
    across Python, TypeScript, JavaScript, Rust, Go, C, C++, Java, Kotlin, Swift, and C#.
    """
    parser = Parser(language)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    line_offsets = [0] + [i + 1 for i, b in enumerate(source_bytes) if b == 10]

    def get_line(byte_offset: int) -> int:
        return bisect.bisect_right(line_offsets, byte_offset)

    declarations: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []

    def node_str(n: TSNode) -> str:
        return source_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace").strip()

    stack: List[Tuple[TSNode, Optional[str]]] = [(top_child, None) for top_child in reversed(root.children)]
    while stack:
        node, current_class = stack.pop()
        actual = node

        # Unwrap Python decorated definitions & JS/TS exports
        if node.type == "decorated_definition":
            for c in node.children:
                if c.type in (
                    "function_definition", "class_definition", "async_function_definition",
                    "function_declaration", "class_declaration", "method_definition",
                ):
                    actual = c
                    break
        elif node.type in ("export_statement", "export_default_statement"):
            for c in node.children:
                if c.type in (
                    "class_declaration", "function_declaration", "interface_declaration",
                    "type_alias_declaration", "lexical_declaration", "variable_declaration",
                ):
                    actual = c
                    break

        # ── 1. Imports ──────────────────────────────────────────────────────────
        if actual.type == "import_statement":
            line = get_line(actual.start_byte)
            for c in actual.children:
                if c.type in ("dotted_name", "identifier"):
                    imports.append({"symbol": node_str(c), "module": node_str(c), "line": line})
                elif c.type == "import_clause":
                    mod = ""
                    for sc in actual.children:
                        if sc.type == "string":
                            mod = node_str(sc).strip("\"'")
                    for sc in c.children:
                        if sc.type == "identifier":
                            imports.append({"symbol": node_str(sc), "module": mod, "line": line})
                        elif sc.type == "named_imports":
                            for spec in sc.children:
                                if spec.type == "import_specifier":
                                    name = get_identifier(spec, source_bytes)
                                    if name:
                                        imports.append({"symbol": name, "module": mod, "line": line})
                        elif sc.type == "namespace_import":
                            name = get_identifier(sc, source_bytes)
                            if name:
                                imports.append({"symbol": name, "module": mod, "line": line})

        elif actual.type == "import_from_statement":
            line = get_line(actual.start_byte)
            mod = ""
            for c in actual.children:
                if c.type in ("dotted_name", "relative_import") and not mod:
                    mod = node_str(c)
                elif c.type == "dotted_name" and mod:
                    imports.append({"symbol": node_str(c), "module": mod, "line": line})
                elif c.type == "aliased_import":
                    name = get_identifier(c, source_bytes)
                    if name:
                        imports.append({"symbol": name, "module": mod, "line": line})

        elif actual.type == "use_declaration":
            # Rust: use std::collections::HashMap; or use serde::{Serialize, Deserialize};
            line = get_line(actual.start_byte)
            arg = actual.child_by_field_name("argument")
            if arg:
                if arg.type == "scoped_identifier":
                    path_node = arg.child_by_field_name("path")
                    name_node = arg.child_by_field_name("name")
                    if name_node:
                        imports.append({
                            "symbol": node_str(name_node),
                            "module": node_str(path_node) if path_node else "",
                            "line": line,
                        })
                elif arg.type == "scoped_use_list":
                    path_node = arg.child_by_field_name("path")
                    list_node = arg.child_by_field_name("list")
                    mod_name = node_str(path_node) if path_node else ""
                    if list_node:
                        for item in list_node.children:
                            if item.type in ("identifier", "scoped_identifier"):
                                name = get_identifier(item, source_bytes)
                                if name:
                                    imports.append({"symbol": name, "module": mod_name, "line": line})
                elif arg.type == "identifier":
                    imports.append({"symbol": node_str(arg), "module": node_str(arg), "line": line})

        elif actual.type == "import_declaration":
            # Go: import "fmt" or import ( "net/http" )
            line = get_line(actual.start_byte)
            for c in actual.children:
                specs = [c] if c.type == "import_spec" else (c.children if c.type == "import_spec_list" else [])
                for spec in specs:
                    if spec.type == "import_spec":
                        p = spec.child_by_field_name("path")
                        if p:
                            mod_path = node_str(p).strip("\"'")
                            sym_name = mod_path.split("/")[-1]
                            alias = spec.child_by_field_name("name")
                            if alias:
                                sym_name = node_str(alias)
                            imports.append({"symbol": sym_name, "module": mod_path, "line": line})

        elif actual.type in ("preproc_include", "using_directive", "import_header"):
            line = get_line(actual.start_byte)
            path_node = actual.child_by_field_name("path") or actual.child_by_field_name("name")
            sym = node_str(path_node or actual).strip("\"'<> ;")
            imports.append({"symbol": sym, "module": sym, "line": line})

        # ── 2. Declarations ─────────────────────────────────────────────────────
        elif actual.type in (
            "class_definition", "class_declaration", "interface_declaration", "type_alias_declaration",
            "class_specifier", "struct_specifier", "namespace_definition", "namespace_declaration",
            "record_declaration", "object_declaration", "protocol_declaration",
        ):
            c_name = get_identifier(actual, source_bytes) or "AnonymousClass"
            body = get_body_node(actual)
            sig = get_signature_text(actual, body, source_bytes)
            doc = extract_first_docstring_text(body, source_bytes) if body else None
            if "interface" in actual.type or "protocol" in actual.type:
                kind = "interface"
            elif "type_alias" in actual.type:
                kind = "type"
            elif "struct" in actual.type:
                kind = "struct"
            elif "namespace" in actual.type:
                kind = "namespace"
            else:
                kind = "class"
            declarations.append({
                "name": c_name,
                "kind": kind,
                "line": get_line(actual.start_byte),
                "signature": sig,
                "docstring": doc,
                "parent": current_class,
                "file_path": file_path,
            })
            if body:
                for child in reversed(body.children):
                    stack.append((child, c_name))
            continue

        # Rust struct, enum, trait
        elif actual.type in ("struct_item", "enum_item", "trait_item"):
            s_name = get_identifier(actual, source_bytes) or "AnonymousItem"
            body = get_body_node(actual)
            sig = get_signature_text(actual, body, source_bytes)
            doc = extract_first_docstring_text(actual, source_bytes)
            kind = "struct" if actual.type == "struct_item" else ("enum" if actual.type == "enum_item" else "trait")
            declarations.append({
                "name": s_name,
                "kind": kind,
                "line": get_line(actual.start_byte),
                "signature": sig,
                "docstring": doc,
                "parent": current_class,
                "file_path": file_path,
            })
            continue

        # Rust impl block
        elif actual.type == "impl_item":
            impl_type = actual.child_by_field_name("type")
            impl_name = node_str(impl_type) if impl_type else "Impl"
            body = get_body_node(actual)
            if body:
                for child in reversed(body.children):
                    stack.append((child, impl_name))
            continue

        # Go type declaration (structs / interfaces)
        elif actual.type == "type_declaration":
            for ts in actual.children:
                if ts.type == "type_spec":
                    t_name = get_identifier(ts, source_bytes) or "AnonymousType"
                    sig = node_str(ts)
                    doc = extract_first_docstring_text(actual, source_bytes)
                    declarations.append({
                        "name": t_name,
                        "kind": "type",
                        "line": get_line(actual.start_byte),
                        "signature": sig[:160],
                        "docstring": doc,
                        "parent": None,
                        "file_path": file_path,
                    })
            continue

        # Go method declaration: func (s *Server) Start()
        elif actual.type == "method_declaration":
            m_name = get_identifier(actual, source_bytes) or "anonymous_method"
            rec_node = actual.child_by_field_name("receiver")
            rec_type = None
            if rec_node:
                def _find_type_ident(n: TSNode) -> Optional[str]:
                    if n.type == "type_identifier":
                        return node_str(n)
                    for c in n.children:
                        res = _find_type_ident(c)
                        if res:
                            return res
                    return None
                rec_type = _find_type_ident(rec_node)
            body = get_body_node(actual)
            sig = get_signature_text(actual, body, source_bytes)
            doc = extract_first_docstring_text(actual, source_bytes)
            parent_cls = rec_type or current_class
            declarations.append({
                "name": m_name,
                "kind": "method",
                "line": get_line(actual.start_byte),
                "signature": sig,
                "docstring": doc,
                "parent": parent_cls,
                "file_path": file_path,
            })
            if body:
                for child in reversed(body.children):
                    stack.append((child, parent_cls))
            continue

        # Functions across Python, JS, TS, Rust, Go, C/C++, Java, Swift, Kotlin, C#
        elif actual.type in (
            "function_definition", "async_function_definition",
            "function_declaration", "method_definition", "generator_function_declaration",
            "function_item", "constructor_declaration", "initializer_declaration",
        ):
            f_name = get_identifier(actual, source_bytes) or "anonymous_fn"
            body = get_body_node(actual)
            sig = get_signature_text(actual, body, source_bytes)
            doc = extract_first_docstring_text(body or actual, source_bytes)
            kind = "method" if (current_class or actual.type in ("method_definition", "constructor_declaration", "initializer_declaration")) else "function"
            declarations.append({
                "name": f_name,
                "kind": kind,
                "line": get_line(actual.start_byte),
                "signature": sig,
                "docstring": doc,
                "parent": current_class,
                "file_path": file_path,
            })
            if body:
                for child in reversed(body.children):
                    stack.append((child, current_class))
            continue

        # JS/TS arrow functions assigned to const/let: const foo = () => ...
        elif actual.type in ("lexical_declaration", "variable_declaration"):
            for vd in actual.children:
                if vd.type == "variable_declarator":
                    val = vd.child_by_field_name("value")
                    if val and val.type in ("arrow_function", "function_expression"):
                        fn_name = get_identifier(vd, source_bytes) or "anonymous_fn"
                        body = get_body_node(val)
                        sig = get_signature_text(val, body, source_bytes)
                        declarations.append({
                            "name": fn_name,
                            "kind": "function",
                            "line": get_line(vd.start_byte),
                            "signature": f"const {fn_name} = {sig}",
                            "docstring": None,
                            "parent": current_class,
                            "file_path": file_path,
                        })
                        if body:
                            for child in reversed(body.children):
                                stack.append((child, current_class))
            continue

        # ── 3. References (calls & instantiations) ──────────────────────────────
        elif actual.type in (
            "call", "call_expression", "new_expression",
            "method_invocation", "object_creation_expression", "invocation_expression",
        ):
            fn_node = (
                actual.child_by_field_name("function")
                or actual.child_by_field_name("name")
                or actual.child_by_field_name("type")
            )
            if not fn_node and len(actual.children) > 0:
                fn_node = actual.children[1] if (actual.type in ("new_expression", "object_creation_expression") and len(actual.children) > 1) else actual.children[0]

            called_ident = None
            if fn_node:
                if fn_node.type in ("attribute", "member_expression"):
                    prop = fn_node.child_by_field_name("attribute") or fn_node.child_by_field_name("property")
                    if prop:
                        called_ident = node_str(prop)
                elif fn_node.type == "selector_expression":  # Go
                    field = fn_node.child_by_field_name("field")
                    if field:
                        called_ident = node_str(field)
                elif fn_node.type in ("scoped_identifier", "qualified_identifier"):  # Rust & C++ (std::cout)
                    scoped_name = fn_node.child_by_field_name("name")
                    if scoped_name:
                        called_ident = node_str(scoped_name)
                elif fn_node.type in ("field_expression", "field_access"):  # Rust self.process() & C/C++ ptr->process()
                    field = fn_node.child_by_field_name("field")
                    if field:
                        called_ident = node_str(field)
                elif fn_node.type == "navigation_expression":  # Swift & Kotlin (obj.method())
                    suffix = fn_node.child_by_field_name("suffix")
                    if suffix:
                        sub_suffix = suffix.child_by_field_name("suffix") or suffix
                        called_ident = get_identifier(sub_suffix, source_bytes)
                    elif len(fn_node.children) > 1:
                        called_ident = get_identifier(fn_node.children[-1], source_bytes)
                else:
                    called_ident = get_identifier(fn_node, source_bytes)

            if called_ident and len(called_ident) > 1:
                references.append({
                    "symbol": called_ident,
                    "line": get_line(actual.start_byte),
                    "kind": "call",
                    "file_path": file_path,
                })

        for child in reversed(actual.children):
            stack.append((child, current_class))

    return {
        "file_path": file_path,
        "declarations": declarations,
        "imports": imports,
        "references": references,
    }
