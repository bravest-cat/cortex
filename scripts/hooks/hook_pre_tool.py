#!/usr/bin/env python3
"""
PreToolUse hook: safety gate and blast radius interceptor.
"""
import re
import sys
from pathlib import Path

# Add hook directory to path for common imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import emit_hook_response, get_hook_payload

DESTRUCTIVE_COMMAND_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+[/~]",   # rm -rf / or ~
    r"\bgit\s+reset\s+--hard\b",              # git reset --hard
    r"\bdocker\s+system\s+prune\s+-a\b",      # docker system prune -a
    r"\bfalkordb.*GRAPH\.DELETE\b",           # deleting whole falkordb graphs
]

CORE_ARCHITECTURAL_FILES = {
    "server.py", "graph.py", "engine.py", "driver.py", "dream.py"
}

def is_destructive_command(cmd: str) -> bool:
    for pattern in DESTRUCTIVE_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False

def main():
    payload = get_hook_payload()
    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args", {})

    if tool_name == "run_command":
        cmd = args.get("CommandLine", "")
        if is_destructive_command(cmd):
            emit_hook_response({
                "decision": "ask",
                "reason": f"⚠️ [Cortex Safety Gate] High-risk shell command detected: '{cmd[:80]}...'. Requires confirmation."
            })

    elif tool_name in ("write_to_file", "replace_file_content", "multi_replace_file_content"):
        target_file = args.get("TargetFile", "")
        file_name = target_file.split("/")[-1]
        if file_name in CORE_ARCHITECTURAL_FILES and "cortex/cortex/" in target_file:
            print(f"[Cortex PreTool] ℹ️  Modifying core architectural file {file_name}. Ensure trace_symbol_impact was run.", file=sys.stderr)

    emit_hook_response({"decision": "allow"})

if __name__ == "__main__":
    main()
