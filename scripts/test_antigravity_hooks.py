#!/usr/bin/env python3
"""
Automated Test Suite for Antigravity Lifecycle Hooks Suite.

Tests:
  1. PreInvocation hook (turn 0 invariant injection, turn 1 pass-through, turn 10 refresher).
  2. PreToolUse hook (allow safe commands, gate destructive commands).
  3. PostToolUse hook (hot-sync trigger and {} return).
  4. Stop hook (smart outcome enforcement vs read-only pass-through).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent / "hooks"

def test_pre_invoke():
    script = HOOKS_DIR / "hook_pre_invoke.py"

    # Turn 0
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"invocationNum": 0}))
    res0 = json.loads(out)
    assert "injectSteps" in res0, "Turn 0 must inject steps"
    assert "Cortex" in res0["injectSteps"][0]["ephemeralMessage"], "Turn 0 message must mention Cortex"
    print("✅ PreInvocation Turn 0: Injected invariants and health successfully.")

    # Turn 1
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"invocationNum": 1}))
    res1 = json.loads(out)
    assert res1 == {}, "Turn 1 must be empty JSON"
    print("✅ PreInvocation Turn 1: Zero latency empty response verified.")

    # Turn 10
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"invocationNum": 10}))
    res10 = json.loads(out)
    assert "injectSteps" in res10, "Turn 10 must inject steps"
    print("✅ PreInvocation Turn 10: Periodic reminder injected successfully.")

def test_pre_tool():
    script = HOOKS_DIR / "hook_pre_tool.py"

    # Safe tool
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"toolCall": {"name": "view_file", "args": {"AbsolutePath": "/test.py"}}}))
    res_safe = json.loads(out)
    assert res_safe.get("decision") == "allow", "Safe tool should be allowed"
    print("✅ PreToolUse Safe: Standard view_file allowed immediately.")

    # Destructive command
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf /something"}}}))
    res_danger = json.loads(out)
    assert res_danger.get("decision") == "ask", "Destructive command must prompt with 'ask'"
    print("✅ PreToolUse Safety Gate: 'rm -rf /' caught and gated.")

def test_post_tool():
    script = HOOKS_DIR / "hook_post_tool.py"

    # Safe call
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"toolCall": {"name": "view_file", "args": {}}}))
    res = json.loads(out)
    assert res == {}, "PostToolUse must return {}"
    print("✅ PostToolUse: Returned {} conforming to protocol.")

def test_stop_guard():
    script = HOOKS_DIR / "hook_stop.py"

    # Case A: Read-only session (no write_to_file) -> stop allowed
    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"executionNum": 1, "terminationReason": "model_stop", "fullyIdle": True, "transcriptPath": ""}))
    res_readonly = json.loads(out)
    assert res_readonly.get("decision") == "stop", "Read-only session must be allowed to stop"
    print("✅ Stop Hook Read-Only: Allowed termination with zero friction.")

    # Case B: File modified without outcome -> continue required
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/dummy.py"}}]}) + "\n")
        path_b = f.name

    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"executionNum": 1, "terminationReason": "model_stop", "fullyIdle": True, "transcriptPath": path_b}))
    res_mod = json.loads(out)
    assert res_mod.get("decision") == "continue", "Unpersisted modification must return continue"
    print("✅ Stop Hook Guard: Intercepted unpersisted file changes.")

    # Case C: File modified WITH record_system_outcome -> stop allowed
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/dummy.py"}}]}) + "\n")
        f.write(json.dumps({"tool_calls": [{"name": "record_system_outcome", "args": {}}]}) + "\n")
        path_c = f.name

    p = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps({"executionNum": 1, "terminationReason": "model_stop", "fullyIdle": True, "transcriptPath": path_c}))
    res_done = json.loads(out)
    assert res_done.get("decision") == "stop", "Persisted modification must be allowed to stop"
    print("✅ Stop Hook Persisted: Allowed termination once outcome was recorded.")

if __name__ == "__main__":
    print("=== Running Antigravity Lifecycle Hooks Test Suite ===")
    test_pre_invoke()
    test_pre_tool()
    test_post_tool()
    test_stop_guard()
    print("🎉 ALL LIFECYCLE HOOK TESTS PASSED SUCCESSFULLY!")
