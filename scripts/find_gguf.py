#!/usr/bin/env python3
"""
Utility: resolve a GGUF model path from the HuggingFace cache by partial name.
Used by the Makefile and start scripts to avoid hardcoding blob hashes.

Usage:
  python3 find_gguf.py Qwen3-Embedding-8B
  python3 find_gguf.py Qwen3-Reranker
"""
import glob
import os
import sys


def find_gguf(partial_name: str) -> str:
    hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
    pattern  = f"{hf_cache}/**/snapshots/**/*{partial_name}*.gguf"
    matches  = sorted(glob.glob(pattern, recursive=True))

    # Also search blobs directory (HF sometimes stores GGUFs there)
    blob_pattern = f"{hf_cache}/**/*.gguf"
    matches += sorted(glob.glob(blob_pattern, recursive=True))

    # Fallback: check home directory
    home_pattern = os.path.expanduser(f"~/*{partial_name}*.gguf")
    matches += sorted(glob.glob(home_pattern))

    # Deduplicate
    seen = set()
    unique = []
    for m in matches:
        if m not in seen and partial_name.lower().split("-")[0] in m.lower() or len(matches) == 1:
            seen.add(m)
            unique.append(m)

    if not unique:
        print(f"❌ No GGUF found matching '{partial_name}'", file=sys.stderr)
        sys.exit(1)

    return unique[0]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: find_gguf.py <partial-model-name>", file=sys.stderr)
        sys.exit(1)
    print(find_gguf(sys.argv[1]))
