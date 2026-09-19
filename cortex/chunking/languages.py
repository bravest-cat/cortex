"""
Tree-sitter Language Grammars and Extension Mappings for Cortex.

Supports 10 core language ecosystems across Tier 1 (Modern Web/Systems)
and Tier 2 (Enterprise, Native & Systems).
"""
from __future__ import annotations

from typing import Any, Dict, Set

# Tree-sitter bindings
try:
    from tree_sitter import Language, Node as TSNode, Parser
    _TS_AVAILABLE = True
except ImportError:
    Language = Any  # type: ignore
    TSNode = Any    # type: ignore
    Parser = Any    # type: ignore
    _TS_AVAILABLE = False

LANG_MAP: Dict[str, Language] = {}

if _TS_AVAILABLE:
    # ── Tier 1: Core Modern Stack ──────────────────────────────────────────
    try:
        import tree_sitter_python as tspython
        LANG_MAP[".py"] = Language(tspython.language())
    except ImportError:
        pass

    try:
        import tree_sitter_javascript as tsjs
        js_lang = Language(tsjs.language())
        LANG_MAP[".js"] = js_lang
        LANG_MAP[".jsx"] = js_lang
    except ImportError:
        pass

    try:
        import tree_sitter_typescript as tsts
        LANG_MAP[".ts"] = Language(tsts.language_typescript())
        LANG_MAP[".tsx"] = Language(tsts.language_tsx())
    except ImportError:
        pass

    try:
        import tree_sitter_rust as tsrust
        LANG_MAP[".rs"] = Language(tsrust.language())
    except ImportError:
        pass

    try:
        import tree_sitter_go as tsgo
        LANG_MAP[".go"] = Language(tsgo.language())
    except ImportError:
        pass

    # ── Tier 2: Systems, Mobile & Enterprise ────────────────────────────────
    try:
        import tree_sitter_c as tsc
        c_lang = Language(tsc.language())
        LANG_MAP[".c"] = c_lang
        LANG_MAP[".h"] = c_lang
    except ImportError:
        pass

    try:
        import tree_sitter_cpp as tscpp
        cpp_lang = Language(tscpp.language())
        LANG_MAP[".cpp"] = cpp_lang
        LANG_MAP[".hpp"] = cpp_lang
        LANG_MAP[".cc"] = cpp_lang
        LANG_MAP[".cxx"] = cpp_lang
    except ImportError:
        pass

    try:
        import tree_sitter_java as tsjava
        LANG_MAP[".java"] = Language(tsjava.language())
    except ImportError:
        pass

    try:
        import tree_sitter_kotlin as tskotlin
        kt_lang = Language(tskotlin.language())
        LANG_MAP[".kt"] = kt_lang
        LANG_MAP[".kts"] = kt_lang
    except ImportError:
        pass

    try:
        import tree_sitter_swift as tsswift
        LANG_MAP[".swift"] = Language(tsswift.language())
    except ImportError:
        pass

    try:
        import tree_sitter_c_sharp as tscsharp
        LANG_MAP[".cs"] = Language(tscsharp.language())
    except ImportError:
        pass

# Backward compatibility aliases
PY_LANG = LANG_MAP.get(".py")
JS_LANG = LANG_MAP.get(".js")
TS_LANG = LANG_MAP.get(".ts")
TSX_LANG = LANG_MAP.get(".tsx")
RUST_LANG = LANG_MAP.get(".rs")
GO_LANG = LANG_MAP.get(".go")

# Canonical extensions
SUPPORTED_CODE_EXTS: Set[str] = set(LANG_MAP.keys())
SUPPORTED_CONFIG_EXTS: Set[str] = {".yaml", ".yml", ".json", ".toml"}
SUPPORTED_DOC_EXTS: Set[str] = {".md", ".markdown", ".txt"}
ALL_SUPPORTED_EXTS: Set[str] = (
    (SUPPORTED_CODE_EXTS if SUPPORTED_CODE_EXTS else {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go",
        ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
        ".java", ".kt", ".kts", ".swift", ".cs"
    })
    | SUPPORTED_CONFIG_EXTS
    | SUPPORTED_DOC_EXTS
)


def print_language_status():
    """Prints the status of all supported Tree-sitter language grammars."""
    all_langs = [
        ("Python", [".py"], "tree-sitter-python"),
        ("JavaScript / TypeScript", [".js", ".jsx", ".ts", ".tsx"], "tree-sitter-javascript, tree-sitter-typescript"),
        ("Rust", [".rs"], "tree-sitter-rust"),
        ("Go", [".go"], "tree-sitter-go"),
        ("C", [".c", ".h"], "tree-sitter-c"),
        ("C++", [".cpp", ".hpp", ".cc", ".cxx"], "tree-sitter-cpp"),
        ("Java", [".java"], "tree-sitter-java"),
        ("Kotlin", [".kt", ".kts"], "tree-sitter-kotlin"),
        ("Swift", [".swift"], "tree-sitter-swift"),
        ("C#", [".cs"], "tree-sitter-c-sharp"),
    ]
    print("\n=== Cortex Tree-sitter Language Support Status ===")
    print(f"{'Language Family':<26} {'Extensions':<24} {'Status':<12}")
    print("-" * 64)
    for name, exts, _ in all_langs:
        is_active = any(ext in LANG_MAP for ext in exts)
        status_str = "✅ Active" if is_active else "⚪ Inactive"
        exts_str = ", ".join(exts)
        print(f"{name:<26} {exts_str:<24} {status_str:<12}")
    print("-" * 64)
