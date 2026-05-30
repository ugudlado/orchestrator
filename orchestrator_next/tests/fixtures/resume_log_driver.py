#!/usr/bin/env python3
"""Minimal driver-loop fixture simulating SKILL.md's resume_step handler contract.

Reads a JSON action payload on stdin. If action['action'] == 'resume_step',
emits the mandated log line to stderr per SKILL.md: "RESUMING step <id> (attempt <N>)".

Used by test_dispatch_resume.py to verify AC-9 — that the driver contract is
executable and the log fires on every resume, autonomous runs included.
"""
import json
import os
import sys


def main() -> int:
    payload = sys.stdin.read().strip()
    if not payload:
        print("fixture error: no stdin payload", file=sys.stderr)
        return 2
    try:
        action = json.loads(payload)
    except json.JSONDecodeError as exc:
        print(f"fixture error: invalid JSON — {exc}", file=sys.stderr)
        return 2

    autopilot = os.environ.get("AUTOPILOT", "false").lower() == "true"

    if action.get("is_resume"):  # ORC-45: check is_resume flag instead of action field
        step_id = action.get("step_id", "<unknown>")
        attempt = action.get("attempt", "?")
        # Per SKILL.md contract: log even on autopilot runs.
        print(f"RESUMING step {step_id} (attempt {attempt})", file=sys.stderr)
        # Dispatch machinery would run here; fixture just exits.
        return 0

    # Non-resume actions: no log.
    if autopilot:
        pass  # autopilot makes no difference for non-resume.
    return 0


if __name__ == "__main__":
    sys.exit(main())
