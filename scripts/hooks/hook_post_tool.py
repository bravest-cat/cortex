#!/usr/bin/env python3
"""
PostToolUse hook: instant hot-sync for modified files.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add hook directory to path for common imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import emit_hook_response, get_hook_payload

from cortex.chunking.languages import ALL_SUPPORTED_EXTS

SUPPORTED_EXTENSIONS = ALL_SUPPORTED_EXTS | {".md", ".markdown"}

import contextlib

async def run_sync(target_file: str):
    try:
        from cortex.watcher import CortexIncrementalSync
        sync_engine = CortexIncrementalSync()
        with contextlib.redirect_stdout(sys.stderr):
            await asyncio.wait_for(sync_engine.sync_file(target_file), timeout=4.0)
    except Exception as e:
        print(f"[Cortex Hot-Sync] ⚠️  Hot-sync error on {target_file}: {e}", file=sys.stderr)

def main():
    payload = get_hook_payload()

    # If the tool call itself failed with an error, skip hot-sync
    if payload.get("error"):
        emit_hook_response({})

    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args", {})

    if tool_name in ("write_to_file", "replace_file_content", "multi_replace_file_content"):
        target_file = args.get("TargetFile", "")
        if target_file and os.path.isfile(target_file):
            ext = Path(target_file).suffix.lower()
            if ext in SUPPORTED_EXTENSIONS:
                try:
                    asyncio.run(run_sync(target_file))
                except Exception:
                    pass

    emit_hook_response({})

if __name__ == "__main__":
    main()
