"""
Structured Configuration File Splitter for Cortex (.yaml, .yml, .json, .toml).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

try:
    import tomllib
    _TOMLLIB_AVAILABLE = True
except ImportError:
    _TOMLLIB_AVAILABLE = False


def _fallback_count_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class ConfigSectionSplitter:
    """
    Parses structured configuration files (.yaml, .yml, .toml, .json)
    by logical sections or keys, with syntax verification and breadcrumbs.
    """

    def __init__(self, max_tokens: int = 512):
        self.max_tokens = max_tokens

    def split_config(self, text: str, file_path: str = "") -> List[Dict[str, Any]]:
        ext = Path(file_path).suffix.lower()
        chunks: List[Dict[str, Any]] = []

        if ext == ".json":
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return self._split_dict_sections(data, file_path, "json")
            except Exception:
                pass
        elif ext in (".yaml", ".yml") and _YAML_AVAILABLE:
            try:
                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    return self._split_dict_sections(data, file_path, "yaml")
            except Exception:
                pass
        elif ext == ".toml" and _TOMLLIB_AVAILABLE:
            try:
                data = tomllib.loads(text)
                if isinstance(data, dict):
                    return self._split_dict_sections(data, file_path, "toml")
            except Exception:
                pass

        # Fallback to single chunk or raw text
        header = f"# File: {file_path}\n# Config File\n\n"
        return [{
            "text": header + text[:2048],
            "scope": "config",
            "chunk_type": "config_section",
            "start_char": 0,
            "end_char": len(text),
        }]

    def _split_dict_sections(self, data: Dict[str, Any], file_path: str, fmt: str) -> List[Dict[str, Any]]:
        chunks = []
        buffered_items: Dict[str, Any] = {}
        buffered_tokens = 0

        def flush_config():
            nonlocal buffered_items, buffered_tokens
            if not buffered_items:
                return
            keys = list(buffered_items.keys())
            scope_str = f"config sections: {', '.join(keys[:3])}"
            if len(keys) > 3:
                scope_str += f" (+{len(keys) - 3} more)"
            header = f"# File: {file_path}\n# Scope: {scope_str}\n\n"

            if fmt == "json":
                body = json.dumps(buffered_items, indent=2)
            elif fmt == "yaml" and _YAML_AVAILABLE:
                body = yaml.dump(buffered_items, default_flow_style=False)
            else:
                body = str(buffered_items)

            chunks.append({
                "text": header + body,
                "scope": scope_str,
                "chunk_type": "config_section",
                "start_char": 0,
                "end_char": len(body),
            })
            buffered_items = {}
            buffered_tokens = 0

        for k, v in data.items():
            if fmt == "json":
                item_str = json.dumps({k: v}, indent=2)
            elif fmt == "yaml" and _YAML_AVAILABLE:
                item_str = yaml.dump({k: v}, default_flow_style=False)
            else:
                item_str = f"{k} = {v}"

            item_toks = _fallback_count_tokens(item_str)
            if item_toks > self.max_tokens:
                flush_config()
                header = f"# File: {file_path}\n# Scope: section [{k}]\n\n"
                chunks.append({
                    "text": header + item_str[:2048],
                    "scope": f"section [{k}]",
                    "chunk_type": "config_section",
                    "start_char": 0,
                    "end_char": len(item_str),
                })
            elif buffered_tokens + item_toks > self.max_tokens and buffered_items:
                flush_config()
                buffered_items[k] = v
                buffered_tokens = item_toks
            else:
                buffered_items[k] = v
                buffered_tokens += item_toks

        flush_config()
        return chunks
