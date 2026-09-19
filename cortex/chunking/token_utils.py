"""
Token Estimation, Git Temporal Metadata and AST Docstring Utilities for Cortex.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from tree_sitter import Node as TSNode
except ImportError:
    TSNode = None


def count_tokens(text: str) -> int:
    """Accurately counts tokens via fast ~4 chars/token heuristic without network blocking."""
    if not text:
        return 0
    return max(1, len(text) // 4)


_GIT_TEMPORAL_CACHE: Dict[str, Optional[Dict[str, str]]] = {}


def get_file_temporal_info(file_path: str) -> Optional[Dict[str, str]]:
    """
    Extracts commit hash, commit date, and commit message for a file.
    If not in a git repository or untracked, falls back to filesystem mtime.
    Cached in-memory per file path.
    """
    if not file_path:
        return None
    if file_path in _GIT_TEMPORAL_CACHE:
        return _GIT_TEMPORAL_CACHE[file_path]

    p = Path(file_path)
    res_dict: Optional[Dict[str, str]] = None

    if p.exists():
        # 1. Try git log
        try:
            cwd_dir = str(p.parent) if p.parent.exists() else None
            res = subprocess.run(
                ["git", "log", "-1", "--format=%h|%as|%s", "--", p.name],
                cwd=cwd_dir,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("|", 2)
                if len(parts) == 3:
                    res_dict = {
                        "commit_hash": parts[0],
                        "commit_date": parts[1],
                        "commit_msg": parts[2],
                    }
        except Exception:
            pass

        # 2. Fallback to filesystem mtime
        if not res_dict:
            try:
                mtime = os.path.getmtime(file_path)
                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                res_dict = {
                    "commit_hash": "local",
                    "commit_date": mtime_str,
                    "commit_msg": "Working tree modification",
                }
            except Exception:
                res_dict = None

    if not res_dict:
        res_dict = {
            "commit_hash": "local",
            "commit_date": datetime.now().strftime("%Y-%m-%d"),
            "commit_msg": "Working tree modification",
        }

    _GIT_TEMPORAL_CACHE[file_path] = res_dict
    return res_dict


def extract_first_docstring_text(node: TSNode, source_bytes: bytes) -> Optional[str]:
    """
    Extracts docstring / doc-comment summary across Python, Rust, C/C++, Java, Kotlin, Swift, C#, Go.
    Checks:
    1. Internal docstrings / expression strings (Python, JS)
    2. Immediately preceding sibling doc comments (Rust '///', Swift '///', C# '///', C/C++ '/**', Go '//')
    """
    if node is None:
        return None

    # 1. Internal docstrings/comments (Python, JS)
    for child in node.children:
        if child.type == "expression_statement":
            for sc in child.children:
                if sc.type == "string":
                    raw = source_bytes[sc.start_byte:sc.end_byte].decode("utf-8", errors="replace")
                    clean = raw.strip("\"' \t\n")
                    first_line = clean.splitlines()[0].strip() if clean else ""
                    return first_line[:180] if first_line else None
        elif child.type in ("comment", "line_comment", "block_comment"):
            raw = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            clean = raw.strip("/*#/ \t\n")
            first_line = clean.splitlines()[0].strip() if clean else ""
            return first_line[:180] if first_line else None

    # 2. Preceding sibling comments (Rust '///', Go '//', C/C++ '/**', Swift '///', C# '///')
    prev = node.prev_sibling
    preceding_comments: List[str] = []
    while prev and prev.type in ("comment", "line_comment", "block_comment"):
        raw = source_bytes[prev.start_byte:prev.end_byte].decode("utf-8", errors="replace")
        clean = raw.strip("/*#/ \t\n")
        if clean:
            preceding_comments.append(clean)
        prev = prev.prev_sibling

    if preceding_comments:
        preceding_comments.reverse()
        return " ".join(preceding_comments)[:180]

    return None
