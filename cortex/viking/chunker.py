"""
Viking chunker: tree-sitter based hierarchical code context slicer.

Produces three tiers of structural context from source files:
  L0: file and module summary (docstring and top-level exported names)
  L1: class and function signatures (architectural overview)
  L2: full file content capped at 8K characters (detailed reference)

Acts as the structural memory layer, letting agents understand
subsystem architecture without exceeding context limits.
Supports real-time incremental hot-syncing and dynamic workspace adaptation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from cortex.chunking.languages import (
    LANG_MAP as CANONICAL_LANG_MAP,
    ALL_SUPPORTED_EXTS,
    Parser,
    _TS_AVAILABLE,
)

SUPPORTED_EXT = ALL_SUPPORTED_EXTS
TEXT_EXT = {".md", ".markdown", ".txt", ".yaml", ".yml", ".toml", ".json"}
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", "coverage", ".pytest_cache", ".gemini"
}


@dataclass
class HierarchicalContext:
    file_path: str
    l0_summary: str       # Brief module overview
    l1_architecture: str  # Class/function signatures
    l2_references: str    # Full content (capped)


class VikingChunker:
    """
    Scans workspace roots and produces hierarchical L0/L1/L2 context
    for queries and agent consumption. Supports dynamic workspace switching
    and incremental file synchronization.
    """

    def __init__(self, workspace_root: Optional[str] = None):
        root_str = workspace_root or os.getenv("WORKSPACE_ROOTS") or str(Path.cwd())
        self.roots: List[Path] = [
            Path(p.strip()).resolve() for p in root_str.split(",") if p.strip()
        ]
        self.root: Path = self.roots[0] if self.roots else Path.cwd().resolve()
        self._cache: Dict[str, HierarchicalContext] = {}
        self._scanned_roots: Set[Path] = set()

    def set_active_workspace(self, workspace_root: str | Path):
        """Dynamically switches or prepends the active workspace root."""
        p = Path(workspace_root).resolve()
        if p not in self.roots:
            self.roots.insert(0, p)
        else:
            self.roots.remove(p)
            self.roots.insert(0, p)
        self.root = p
        if p not in self._scanned_roots:
            self._scan_root(p, limit=300)

    def sync_file(self, file_path: Path | str) -> Optional[HierarchicalContext]:
        """
        Incrementally updates the L0/L1/L2 hierarchical context for a single file.
        Invoked by file watchers and lifecycle hot-sync hooks without full re-scan.
        """
        p = Path(file_path).resolve()
        if not p.is_file():
            return None

        root = self._find_owning_root(p)
        ctx = self._parse_file(p, root)
        if ctx:
            self._cache[ctx.file_path] = ctx
            self._cache[str(p)] = ctx
        return ctx

    def delete_file(self, file_path: Path | str) -> bool:
        """Removes a deleted file from the Viking structural cache."""
        p = Path(file_path).resolve()
        str_p = str(p)
        deleted = False

        keys_to_remove = []
        for k, ctx in self._cache.items():
            if k == str_p or ctx.file_path == str_p or ctx.file_path == p.name:
                keys_to_remove.append(k)
                continue
            for r in self.roots:
                try:
                    if (r / ctx.file_path).resolve() == p:
                        keys_to_remove.append(k)
                        break
                except Exception:
                    pass

        for k in set(keys_to_remove):
            self._cache.pop(k, None)
            deleted = True
        return deleted

    def get_context_for_query(
        self,
        query: str,
        max_files: int = 5,
        workspace_root: Optional[str] = None,
    ) -> str:
        """Return L0 + L1 context blocks ranked by relevance to the query."""
        if workspace_root:
            target_p = Path(workspace_root).resolve()
            if target_p not in self.roots:
                self.set_active_workspace(target_p)

        if not self._cache:
            self._scan_workspace(limit=300)

        if not self._cache:
            return "_No structural context available (workspace not yet scanned)._"

        # Keyword-based relevance scoring against path, summary, and signatures
        q_tokens = [t.lower() for t in query.split() if len(t) > 2]
        scored = []
        seen_paths = set()

        for ctx in self._cache.values():
            if ctx.file_path in seen_paths:
                continue
            seen_paths.add(ctx.file_path)

            score = 0
            p_lower = ctx.file_path.lower()
            s_lower = ctx.l0_summary.lower()
            a_lower = ctx.l1_architecture.lower()
            for tok in q_tokens:
                if tok in p_lower:
                    score += 5
                if tok in s_lower:
                    score += 3
                if tok in a_lower:
                    score += 2
            scored.append((score, ctx))

        scored.sort(key=lambda x: x[0], reverse=True)
        matching = [ctx for s, ctx in scored if s > 0]
        selected = matching[:max_files] if matching else [ctx for _, ctx in scored[:max_files]]

        parts = []
        for ctx in selected:
            parts.append(
                f"**`{ctx.file_path}`**\n"
                f"*Summary:* {ctx.l0_summary}\n\n"
                f"*Signatures:*\n```\n{ctx.l1_architecture}\n```"
            )
        return "\n\n---\n\n".join(parts)

    def get_l2_for_file(self, rel_path: str) -> str:
        """Return the detailed L2 content for a specific file."""
        if rel_path in self._cache:
            return self._cache[rel_path].l2_references
        for root in self.roots:
            p = root / rel_path
            if p.exists():
                ctx = self._parse_file(p, root)
                return ctx.l2_references if ctx else ""
        return ""

    def refresh(self, limit: int = 300):
        """Re-scan the workspace (clears cache)."""
        self._cache.clear()
        self._scanned_roots.clear()
        self._scan_workspace(limit=limit)

    # ── Private helpers ────────────────────────────────────────────────────

    def _find_owning_root(self, p: Path) -> Path:
        for r in self.roots:
            try:
                if p.is_relative_to(r):
                    return r
            except AttributeError:
                if str(p).startswith(str(r)):
                    return r
        return self.root

    def _scan_root(self, root: Path, limit: int = 300) -> List[HierarchicalContext]:
        results = []
        if not root.exists():
            return results
        self._scanned_roots.add(root)

        for path in root.rglob("*"):
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(p.startswith(".") or p in EXCLUDE_DIRS for p in rel_parts[:-1]):
                continue
            if (path.suffix in SUPPORTED_EXT or path.suffix in TEXT_EXT) and path.is_file():
                ctx = self._parse_file(path, root)
                if ctx:
                    results.append(ctx)
                    if len(results) >= limit:
                        break
        return results

    def _scan_workspace(self, limit: int = 300) -> List[HierarchicalContext]:
        results = []
        for root in self.roots:
            res = self._scan_root(root, limit=limit - len(results))
            results.extend(res)
            if len(results) >= limit:
                break
        return results

    def _parse_file(self, path: Path, root: Optional[Path] = None) -> Optional[HierarchicalContext]:
        try:
            source_bytes = path.read_bytes()
            source_str = source_bytes.decode("utf-8", errors="replace")
        except (OSError, PermissionError):
            return None

        base_root = root or self._find_owning_root(path)
        try:
            rel = str(path.relative_to(base_root))
        except ValueError:
            rel = str(path)

        ext = path.suffix.lower()
        lang = CANONICAL_LANG_MAP.get(ext) if _TS_AVAILABLE else None

        if lang:
            try:
                parser = Parser(lang)
                tree = parser.parse(source_bytes)
                l0 = self._extract_l0(tree, source_bytes, path.name)
                l1 = self._extract_l1(tree, source_bytes)
            except Exception:
                lines = source_str.split("\n")
                l0 = f"File: {path.name} ({len(lines)} lines)"
                l1 = "\n".join(lines[:30])
        else:
            lines = source_str.split("\n")
            l0 = f"File: {path.name} ({len(lines)} lines)"
            l1 = "\n".join(lines[:30])

        l2 = source_str[:8000]

        ctx = HierarchicalContext(file_path=rel, l0_summary=l0, l1_architecture=l1, l2_references=l2)
        self._cache[rel] = ctx
        self._cache[str(path.resolve())] = ctx
        return ctx

    def _extract_l0(self, tree, source_bytes: bytes, filename: str) -> str:
        """L0: module docstring + top-level exported names."""
        docstring = ""
        top_names: List[str] = []

        for node in tree.root_node.children:
            if not docstring and node.type == "expression_statement":
                text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
                if text.startswith('"""') or text.startswith("'''"):
                    cleaned = text.strip('"""').strip("'''").strip()
                    lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
                    if lines:
                        docstring = lines[0][:200]

            def_node = node
            if node.type == "decorated_definition":
                for c in node.children:
                    if c.type in ("function_definition", "class_definition", "async_function_definition"):
                        def_node = c
                        break

            if def_node.type in (
                "function_definition", "class_definition", "async_function_definition",
                "function_declaration", "class_declaration",
                "export_statement", "method_definition", "struct_item", "impl_item",
            ):
                name = None
                for c in def_node.children:
                    if c.type in ("identifier", "type_identifier"):
                        name = source_bytes[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
                        break
                if name:
                    top_names.append(name)

        summary = docstring or f"Module: {filename}"
        if top_names:
            summary += f"\nDefines: {', '.join(top_names[:10])}"
        return summary

    def _extract_l1(self, tree, source_bytes: bytes) -> str:
        """L1: all class/function signatures."""
        sigs: List[str] = []
        for node in tree.root_node.children:
            def_node = node
            if node.type == "decorated_definition":
                for c in node.children:
                    if c.type in ("function_definition", "class_definition", "async_function_definition"):
                        def_node = c
                        break

            if def_node.type in (
                "function_definition", "class_definition", "async_function_definition",
                "function_declaration", "class_declaration",
                "export_statement", "struct_item", "impl_item",
            ):
                name = None
                body_start = None
                body_node = None
                for c in def_node.children:
                    if c.type in ("identifier", "type_identifier") and not name:
                        name = source_bytes[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
                    if c.type in ("block", "statement_block", "field_declaration_list", "declaration_list", "class_body"):
                        body_start = c.start_byte
                        body_node = c
                        break
                if body_start:
                    raw_sig = source_bytes[def_node.start_byte:body_start].decode("utf-8", errors="replace").strip()
                    sigs.append(f"  {' '.join(raw_sig.split())}")
                    if body_node and ("class" in def_node.type or def_node.type in ("struct_item", "impl_item", "interface_declaration")):
                        for mc in body_node.children:
                            m_actual = mc
                            if mc.type == "decorated_definition":
                                for inner in mc.children:
                                    if inner.type in ("function_definition", "async_function_definition", "method_definition", "function_item", "method_declaration"):
                                        m_actual = inner
                                        break
                            if m_actual.type in ("function_definition", "async_function_definition", "method_definition", "function_item", "method_declaration"):
                                m_body_start = None
                                for m_child in m_actual.children:
                                    if m_child.type in ("block", "statement_block"):
                                        m_body_start = m_child.start_byte
                                        break
                                if m_body_start:
                                    m_sig = source_bytes[m_actual.start_byte:m_body_start].decode("utf-8", errors="replace").strip()
                                    sigs.append(f"    - {' '.join(m_sig.split())}")
                elif name:
                    sigs.append(f"  {name}")

        return "\n".join(sigs) if sigs else "(no top-level definitions found)"
