#!/usr/bin/env python3
"""Read common fields from workflow state.yaml (CLI helpers for step scripts)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


def load(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def change_id(state: dict) -> str:
    return str(state.get("change_id") or state.get("slug") or "")


def ticket_sync_fields(state: dict) -> tuple[str, str]:
    return str(state.get("ticket_id") or ""), str(state.get("ticketing") or "")


def ticket_rework_fields(state: dict) -> tuple[str, str]:
    ticket_id = str(state.get("ticket_id") or "")
    if not ticket_id:
        cid = str(state.get("change_id") or "")
        if re.match(r"^[A-Za-z]+-\d+$", cid) or re.match(r"^task-\d+$", cid, re.I):
            ticket_id = cid
    return ticket_id, str(state.get("branch") or "")


def cost_summary_relpath(state_path: str | Path, summary_path: str | Path) -> str:
    import os

    state = load(state_path)
    root = state.get("worktree_path") or state.get("repo_root") or ""
    summary = str(summary_path)
    if root:
        try:
            return os.path.relpath(summary, root)
        except ValueError:
            return summary
    return summary


def _cli() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: state_yaml.py <change-id|ticket-sync|ticket-rework|cost-relpath> <args...>",
            file=sys.stderr,
        )
        return 2
    cmd, *rest = sys.argv[1], sys.argv[2:]
    if cmd == "change-id":
        print(change_id(load(rest[0])))
        return 0
    if cmd == "ticket-sync":
        tid, ticketing = ticket_sync_fields(load(rest[0]))
        print(tid, ticketing)
        return 0
    if cmd == "ticket-rework":
        tid, branch = ticket_rework_fields(load(rest[0]))
        print(tid, branch)
        return 0
    if cmd == "cost-relpath":
        print(cost_summary_relpath(rest[0], rest[1]))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
