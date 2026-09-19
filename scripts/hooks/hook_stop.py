#!/usr/bin/env python3
"""
Stop hook: enforce outcome persistence on modified workspaces.

Logic:
  1. Intervene ONLY on executionNum == 1 AND terminationReason == 'model_stop'.
  2. Don't block if background tasks are still running (fullyIdle == False).
  3. Scan transcriptPath JSONL:
     - Check if file-modifying tools were actually executed.
     - Check if record_system_outcome or trigger_dream_mode was executed.
     - NEVER string-match raw message content (prevents pre-invoke banner false-positives).
  4. Decision rule:
     - If NO files were modified: allow stop immediately (pure Q&A/research turns exit cleanly).
     - If files WERE modified AND outcome was recorded: allow stop.
     - If files WERE modified AND outcome was NOT recorded: return continue with prompt.
"""
import json
import os
import sys
from pathlib import Path

# Add hook directory to path for common imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import emit_hook_response, get_hook_payload

FILE_MODIFYING_TOOLS = {
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
}

OUTCOME_TOOLS = {
    "record_system_outcome",
    "trigger_dream_mode",
}

def analyze_transcript(transcript_path: str):
    modified_files = False
    recorded_outcome = False

    if not transcript_path or not os.path.isfile(transcript_path):
        return False, True  # Fallback: don't block if transcript is inaccessible

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                except Exception:
                    continue

                tool_calls = step.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    continue

                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    name = tc.get("name") or tc.get("tool_name") or ""
                    args = tc.get("args", {}) or {}
                    target_tool_name = args.get("ToolName", "") if isinstance(args, dict) else ""

                    # Check for outcome recording tools (native, prefixed, or via call_mcp_tool / run_command)
                    for ot in OUTCOME_TOOLS:
                        if ot in name or ot == target_tool_name:
                            recorded_outcome = True
                        elif name == "run_command" and isinstance(args, dict) and ot in args.get("CommandLine", ""):
                            recorded_outcome = True

                    # Check for file modification tools
                    for mt in FILE_MODIFYING_TOOLS:
                        if mt in name:
                            modified_files = True

    except Exception:
        return False, True

    return modified_files, recorded_outcome

def main():
    payload = get_hook_payload()

    execution_num = payload.get("executionNum", 0)
    termination_reason = payload.get("terminationReason", "")
    transcript_path = payload.get("transcriptPath", "")
    fully_idle = payload.get("fullyIdle", True)

    # Only intervene on the first natural model stop
    if execution_num != 1 or termination_reason != "model_stop":
        emit_hook_response({"decision": "stop"})

    # Don't block if background tasks are still running
    if not fully_idle:
        emit_hook_response({"decision": "stop"})

    modified_files, recorded_outcome = analyze_transcript(transcript_path)

    # If no files were modified, exit cleanly without blocking
    if not modified_files:
        emit_hook_response({"decision": "stop"})

    # If files were modified and outcome recorded, allow stop
    if recorded_outcome:
        emit_hook_response({"decision": "stop"})

    # Files were modified but no outcome was recorded
    emit_hook_response({
        "decision": "continue",
        "reason": (
            "🧠 [Cortex Stop Guard] You modified codebase files in this session without "
            "recording an outcome. Please call `record_system_outcome` to persist your decisions, "
            "changes, and verified behavior into Cortex memory before finishing."
        )
    })

if __name__ == "__main__":
    main()
