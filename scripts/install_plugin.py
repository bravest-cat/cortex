#!/usr/bin/env python3
"""
Cortex Antigravity Plugin Dynamic Installer.
Generates machine-specific hooks.json and sidecar.json manifests referencing the
actual repository path on the host machine, then installs symlinks to Antigravity.
"""
import json
import os
import shutil
import stat
from pathlib import Path

CORTEX_ROOT = Path(__file__).resolve().parent.parent
INTEGRATIONS_DIR = CORTEX_ROOT / "integrations" / "antigravity"
SCRIPTS_DIR = CORTEX_ROOT / "scripts" / "hooks"


def build_hooks_dict() -> dict:
    return {
        "cortex-pre-invoke": {
            "PreInvocation": [
                {
                    "type": "command",
                    "command": f"python3 {SCRIPTS_DIR / 'hook_pre_invoke.py'}",
                    "timeout": 5,
                }
            ]
        },
        "cortex-safety-gate": {
            "PreToolUse": [
                {
                    "matcher": "run_command|replace_file_content|write_to_file",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {SCRIPTS_DIR / 'hook_pre_tool.py'}",
                            "timeout": 5,
                        }
                    ],
                }
            ]
        },
        "cortex-hot-sync": {
            "PostToolUse": [
                {
                    "matcher": "write_to_file|replace_file_content|multi_replace_file_content",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {SCRIPTS_DIR / 'hook_post_tool.py'}",
                            "timeout": 15,
                        }
                    ],
                }
            ]
        },
        "cortex-stop-guard": {
            "Stop": [
                {
                    "type": "command",
                    "command": f"python3 {SCRIPTS_DIR / 'hook_stop.py'}",
                    "timeout": 10,
                }
            ]
        },
    }


def build_sidecar_dict() -> dict:
    venv_py = CORTEX_ROOT / ".venv" / "bin" / "python3"
    py_exec = str(venv_py) if venv_py.is_file() else "python3"
    return {
        "name": "cortex-watcher",
        "command": py_exec,
        "args": [
            "-m", "cortex.watcher",
            "--watch", str(CORTEX_ROOT),
        ],
        "restart_policy": "always",
    }


def install_plugin():
    home = Path.home()
    dest_dirs = [
        home / ".gemini" / "antigravity-cli" / "plugins" / "cortex",
        home / ".gemini" / "config" / "plugins" / "cortex",
    ]

    hooks_payload = build_hooks_dict()
    sidecar_payload = build_sidecar_dict()

    for dest in dest_dirs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        # Copy plugin manifest and directories
        if (INTEGRATIONS_DIR / "plugin.json").is_file():
            shutil.copy2(INTEGRATIONS_DIR / "plugin.json", dest / "plugin.json")

        for subdir in ["agents", "skills"]:
            src_sub = INTEGRATIONS_DIR / subdir
            if src_sub.is_dir():
                shutil.copytree(src_sub, dest / subdir, dirs_exist_ok=True)

        # Write dynamically rendered hooks.json into plugin destination
        with open(dest / "hooks.json", "w", encoding="utf-8") as f:
            json.dump(hooks_payload, f, indent=2)

        # Write dynamically rendered sidecar.json into plugin destination
        sidecar_target_dir = dest / "sidecars" / "cortex-watcher"
        sidecar_target_dir.mkdir(parents=True, exist_ok=True)
        with open(sidecar_target_dir / "sidecar.json", "w", encoding="utf-8") as f:
            json.dump(sidecar_payload, f, indent=2)

        print(f"📦 Installed dynamic Cortex plugin to: {dest}")

    # Also install skills and agents to global config
    skills_src = INTEGRATIONS_DIR / "skills"
    agents_src = INTEGRATIONS_DIR / "agents"

    if skills_src.is_dir():
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir():
                target_skill = home / ".gemini" / "config" / "skills" / skill_dir.name
                target_skill.mkdir(parents=True, exist_ok=True)
                for item in skill_dir.glob("*"):
                    shutil.copy2(item, target_skill / item.name)
                print(f"📦 Installed skill: {skill_dir.name}")

    if agents_src.is_dir():
        target_agents = home / ".gemini" / "config" / "agents"
        target_agents.mkdir(parents=True, exist_ok=True)
        for agent_file in agents_src.glob("*.md"):
            shutil.copy2(agent_file, target_agents / agent_file.name)
            print(f"🤖 Installed agent: {agent_file.name}")

    # Make hook scripts executable
    for script in SCRIPTS_DIR.glob("*.py"):
        st = os.stat(script)
        os.chmod(script, st.st_mode | stat.S_IEXEC)


def main():
    print(f"🧠 Installing Cortex Antigravity Plugin from {CORTEX_ROOT}...")
    install_plugin()
    print("🎉 Cortex Antigravity Plugin installed and synchronized dynamically!")


if __name__ == "__main__":
    main()
