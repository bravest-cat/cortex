"""
Hierarchical Markdown Section Splitter for Cortex with breadcrumb preservation.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _fallback_count_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class MarkdownSectionSplitter:
    """Splits Markdown documents on heading boundaries with full hierarchy and breadcrumb tracking."""

    def __init__(self, max_tokens: int = 512):
        self.max_tokens = max_tokens

    def split_markdown(self, text: str, file_path: str = "") -> List[Dict[str, Any]]:
        # 1. Parse Frontmatter if present
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
        doc_title = Path(file_path).stem.replace("-", " ").replace("_", " ").title() if file_path else "Document"
        doc_tags: List[str] = []
        clean_text = text

        if fm_match:
            fm_text = fm_match.group(1)
            clean_text = text[fm_match.end():]
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k_s, v_s = k.strip().lower(), v.strip().strip("\"'")
                    if k_s == "title" and v_s:
                        doc_title = v_s
                    elif k_s in ("tags", "tag") and v_s:
                        doc_tags = [t.strip().lstrip("#") for t in re.split(r"[,;\s\[\]]+", v_s) if t.strip()]

        # 2. Split on header boundaries (H1 to H6)
        sections = re.split(r"(?m)^(?=#{1,6}\s+)", clean_text)
        chunks = []
        curr_offset = 0

        # Stack of (level, heading_text)
        heading_stack: List[Tuple[int, str]] = []

        for sec in sections:
            sec_clean = sec.strip()
            if not sec_clean:
                continue

            first_line = sec_clean.split("\n")[0].strip()
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", first_line)

            if heading_match:
                level = len(heading_match.group(1))
                heading_title = heading_match.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                parent_scope = heading_stack[-1][1] if heading_stack else doc_title
                heading_stack.append((level, heading_title))
            else:
                heading_title = "Overview"
                parent_scope = doc_title

            if heading_stack and heading_stack[0][1].strip().lower() == doc_title.strip().lower():
                breadcrumb_parts = [item[1] for item in heading_stack]
            else:
                breadcrumb_parts = [doc_title] + [item[1] for item in heading_stack]
            breadcrumb_path = " > ".join(breadcrumb_parts)

            tag_header = f"# Tags: {', '.join(doc_tags)}\n" if doc_tags else ""
            breadcrumb = f"# Document: {breadcrumb_path}\n{tag_header}\n"
            sec_tokens = _fallback_count_tokens(breadcrumb + sec_clean)

            chunk_meta = {
                "scope": breadcrumb_path,
                "parent_scope": parent_scope,
                "heading_path": breadcrumb_path,
                "doc_title": doc_title,
                "tags": doc_tags,
                "chunk_type": "markdown_section",
            }

            if sec_tokens <= self.max_tokens:
                chunks.append({
                    "text": breadcrumb + sec_clean,
                    "start_char": curr_offset,
                    "end_char": curr_offset + len(sec_clean),
                    **chunk_meta,
                })
            else:
                paragraphs = sec_clean.split("\n\n")
                curr_body = []
                curr_toks = _fallback_count_tokens(breadcrumb)
                p_offset = curr_offset

                for p in paragraphs:
                    p_toks = _fallback_count_tokens(p)
                    if curr_toks + p_toks > self.max_tokens and curr_body:
                        body_txt = "\n\n".join(curr_body)
                        chunks.append({
                            "text": breadcrumb + body_txt,
                            "start_char": p_offset,
                            "end_char": p_offset + len(body_txt),
                            **chunk_meta,
                        })
                        p_offset += len(body_txt) + 2
                        # Overlap last paragraph
                        curr_body = [curr_body[-1]] if curr_body else []
                        curr_toks = _fallback_count_tokens(breadcrumb) + _fallback_count_tokens(curr_body[0]) if curr_body else _fallback_count_tokens(breadcrumb)

                    curr_body.append(p)
                    curr_toks += p_toks + 2

                if curr_body:
                    body_txt = "\n\n".join(curr_body)
                    chunks.append({
                        "text": breadcrumb + body_txt,
                        "start_char": p_offset,
                        "end_char": p_offset + len(body_txt),
                        **chunk_meta,
                    })

            curr_offset += len(sec)

        return chunks
