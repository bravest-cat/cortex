#!/usr/bin/env python3
"""
Test Suite for Cortex Antigravity Plugin Manifests & Schemas.
"""
import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "integrations" / "antigravity"

def test_plugin_json():
    p = PLUGIN_DIR / "plugin.json"
    assert p.is_file(), "plugin.json must exist"
    data = json.loads(p.read_text())
    assert data.get("name") == "cortex", "Plugin name must be cortex"
    assert re.match(r"^[a-zA-Z0-9-_]+$", data["name"]), "Plugin name must match regex"
    assert "description" in data, "Plugin description required"
    print("✅ plugin.json schema valid.")

def test_mcp_config():
    p = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if not p.is_file():
        print("⚠️ ~/.gemini/config/mcp_config.json not found on host (CI or fresh environment). Skipping host MCP validation.")
        return
    data = json.loads(p.read_text())
    servers = data.get("mcpServers", {})
    assert "cortex" in servers, "cortex server must be defined in mcp_config.json"
    cfg = servers["cortex"]
    assert "cortex-server" in cfg.get("command", ""), "Command must point to cortex-server"
    env = cfg.get("env", {})
    assert env.get("EMBED_BASE_URL") == "http://localhost:8000/v1", "Embed base URL must be :8000"
    assert env.get("RERANK_BASE_URL") == "http://localhost:8000/v1", "Rerank base URL must be :8000"
    assert "Qwen3-Embedding-8B" in env.get("EMBED_MODEL_NAME", ""), "Embed model must match"
    print("✅ mcp_config.json schema and endpoints valid (single canonical 'cortex' entry).")

def test_hooks_json():
    p = PLUGIN_DIR / "hooks.json"
    assert p.is_file(), "hooks.json must exist"
    data = json.loads(p.read_text())
    expected_hooks = ["cortex-pre-invoke", "cortex-safety-gate", "cortex-hot-sync", "cortex-stop-guard"]
    for h in expected_hooks:
        assert h in data, f"Hook {h} must be configured"
    print("✅ hooks.json definitions valid.")

def test_sidecar_json():
    p = PLUGIN_DIR / "sidecars" / "cortex-watcher" / "sidecar.json"
    assert p.is_file(), "sidecar.json must exist"
    data = json.loads(p.read_text())
    assert "command" in data, "Sidecar command required"
    assert data.get("restart_policy") in ("always", "on-failure", "never"), "Valid restart policy required"
    print("✅ sidecars/cortex-watcher/sidecar.json valid.")

def test_subagent_yaml():
    for name in ["cortex-researcher.md", "cortex-architect.md"]:
        p = PLUGIN_DIR / "agents" / name
        assert p.is_file(), f"{name} must exist"
        text = p.read_text()
        assert text.startswith("---"), f"{name} must start with YAML frontmatter"
        assert "name:" in text
        assert "description:" in text
        assert "subagent: true" in text
    print("✅ agents/*.md YAML frontmatters valid.")

def test_skills_yaml():
    for name in ["cortex-rag", "cortex-architect"]:
        p = PLUGIN_DIR / "skills" / name / "SKILL.md"
        assert p.is_file(), f"{name}/SKILL.md must exist"
        text = p.read_text()
        assert text.startswith("---"), f"{name} must start with YAML frontmatter"
        assert f"name: {name}" in text
        assert "description:" in text
    print("✅ skills/*/SKILL.md YAML frontmatters valid.")

if __name__ == "__main__":
    print("=== Running Cortex Plugin Manifest Validation Suite ===")
    test_plugin_json()
    test_mcp_config()
    test_hooks_json()
    test_sidecar_json()
    test_subagent_yaml()
    test_skills_yaml()
    print("🎉 ALL PLUGIN MANIFEST TESTS PASSED SUCCESSFULLY!")
