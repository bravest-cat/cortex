#!/usr/bin/env python3
"""
PreInvocation hook: invariant injection and service health gate.
"""
import re
import sys
from pathlib import Path

# Add hook directory to path for common imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CORTEX_ROOT,
    check_tcp_port,
    emit_hook_response,
    get_hook_payload,
)

INVARIANTS_FILE = CORTEX_ROOT / "journal" / "invariants.md"

def get_active_invariants(max_count: int = 3) -> list:
    if not INVARIANTS_FILE.is_file():
        return []
    try:
        text = INVARIANTS_FILE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"###\s+Invariant\s+\[(.*?)\]\s*\n\*\*Rule:\*\*\s*(.*?)(?=\n\*\*Rationale:|\n###|\Z)",
            re.DOTALL,
        )
        invariants = []
        for match in pattern.finditer(text):
            topic = match.group(1).strip()
            rule = match.group(2).strip().replace("\n", " ")
            invariants.append(f"• **[{topic}]**: {rule}")
            if len(invariants) >= max_count:
                break
        return invariants
    except Exception:
        return []

def main():
    payload = get_hook_payload()
    invocation_num = payload.get("invocationNum", 0)

    # Only fire on turn 0 or every 10th turn
    if invocation_num != 0 and invocation_num % 10 != 0:
        emit_hook_response({})

    # Non-blocking socket pings (FalkorDB: 6379, Qdrant: 6333, oMLX: 8000)
    services_up = {
        "FalkorDB": check_tcp_port("localhost", 6379),
        "Qdrant": check_tcp_port("localhost", 6333),
        "oMLX": check_tcp_port("localhost", 8000),
    }

    down_services = [s for s, ok in services_up.items() if not ok]

    if down_services:
        message = (
            f"⚠️ [Cortex Health Alert] Offline services detected: {', '.join(down_services)}.\n"
            "Run `make start` from your Cortex directory to bring memory and inference online."
        )
    elif invocation_num == 0:
        invariants = get_active_invariants(max_count=3)
        inv_str = "\n".join(invariants) if invariants else "• Invariants synchronized."
        message = (
            "🧠 [Cortex Active Memory]\n"
            "Active Architectural Invariants:\n"
            f"{inv_str}\n\n"
            "Protocols:\n"
            "1. Call `search_knowledge_and_memory` BEFORE writing/modifying code or deciding architecture.\n"
            "2. Call `trace_symbol_impact` BEFORE refactoring functions, classes, or interfaces.\n"
            "3. Call `record_system_outcome` AFTER completing non-trivial work or configuration changes."
        )
    else:
        message = (
            f"🧠 [Cortex Reminder - Turn {invocation_num}]\n"
            "Remember to record significant decisions and outcomes via `record_system_outcome` "
            "before completing the session."
        )

    emit_hook_response({"injectSteps": [{"ephemeralMessage": message}]})

if __name__ == "__main__":
    main()
