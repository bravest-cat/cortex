#!/usr/bin/env python3
"""
Cortex Statusline HUD script for Antigravity CLI.

Reads dynamic agent state JSON on stdin, performs sub-5ms socket health checks,
and renders an ANSI-colored status badge indicating Cortex service health.
"""
import json
import socket
import sys

def check_socket(host: str, port: int, timeout: float = 0.1) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def main():
    # Read optional stdin payload from Antigravity CLI
    payload = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw:
                payload = json.loads(raw)
        except Exception:
            pass

    falkor_ok = check_socket("localhost", 6379)
    qdrant_ok = check_socket("localhost", 6333)
    omlx_ok   = check_socket("localhost", 8000)

    # ANSI colors
    GREEN = "\033[32m"
    RED = "\033[31m"
    GRAY = "\033[90m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

    dot_falkor = f"{GREEN}●{RESET}" if falkor_ok else f"{RED}●{RESET}"
    dot_qdrant = f"{GREEN}●{RESET}" if qdrant_ok else f"{RED}●{RESET}"
    dot_omlx   = f"{GREEN}●{RESET}" if omlx_ok   else f"{RED}●{RESET}"

    # Format statusline
    status = (
        f"{CYAN}🧠 Cortex{RESET} "
        f"[{GRAY}Falkor:{RESET} {dot_falkor} | "
        f"{GRAY}Qdrant:{RESET} {dot_qdrant} | "
        f"{GRAY}oMLX:{RESET} {dot_omlx}]"
    )

    print(status)

if __name__ == "__main__":
    main()
