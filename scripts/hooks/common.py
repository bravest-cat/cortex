#!/usr/bin/env python3
"""
Common utilities and bootstrap logic for Cortex Antigravity Lifecycle Hooks.
"""
import json
import os
import socket
import sys
from pathlib import Path

# Resolve CORTEX_ROOT
CORTEX_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure CORTEX_ROOT is in sys.path
if str(CORTEX_ROOT) not in sys.path:
    sys.path.insert(0, str(CORTEX_ROOT))

# Re-exec under Cortex virtualenv Python if available and not already active
venv_python = CORTEX_ROOT / ".venv" / "bin" / "python3"
if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
    try:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)
    except Exception:
        pass

# Load .env
env_path = CORTEX_ROOT / ".env"
if env_path.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        except Exception:
            pass

def get_hook_payload() -> dict:
    """Safely read and parse the hook JSON payload from stdin."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw else {}
    except Exception:
        return {}

def emit_hook_response(data: dict):
    """Print JSON response to stdout and exit cleanly."""
    print(json.dumps(data))
    sys.exit(0)

def check_tcp_port(host: str, port: int, timeout: float = 0.15) -> bool:
    """Microsecond non-blocking TCP socket check."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False
