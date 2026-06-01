"""Write the initial state.yaml for a workflow run.

Usage:
    python seed_write_state.py <state_yaml_path> <init_json> [prior_state_yaml_path]

Reads identity fields from the most recent prior state file when provided.
Exits non-zero on write failure.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print(
            "Usage: seed_write_state.py <state_yaml_path> <init_json> [prior_state_yaml_path]",
            file=sys.stderr,
        )
        return 1

    state_yaml_path = args[0]
    d = json.loads(args[1])
    prior_path = args[2] if len(args) > 2 else ""

    prior_context: dict = {}
    if prior_path:
        try:
            prior_raw = yaml.safe_load(Path(prior_path).read_text()) or {}
            for key in ("worktree_path", "branch", "repo_root", "change_id", "slug"):
                if prior_raw.get(key):
                    prior_context[key] = prior_raw[key]
        except (OSError, yaml.YAMLError):
            pass  # prior unreadable — start fresh

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {
        "change_id": prior_context.get("change_id") or d["slug"],
        "slug": prior_context.get("slug") or d["slug"],
        "schema": d["schema_name"],
        "status": "active",
        "repo_root": prior_context.get("repo_root") or d["repo_root"],
        "flags": d["flags"],
        "workflow_plan": {"main": {"active": d["active"], "filtered": d["filtered"]}},
        "phase": "main",
        "next_step": {"phase": "main", "step_id": d["active"][0]},
        "step_history": [],
        "created_at": now,
        "started_at": now,
        "project_context_loaded": True,
    }
    if prior_context.get("worktree_path"):
        state["worktree_path"] = prior_context["worktree_path"]
    if prior_context.get("branch"):
        state["branch"] = prior_context["branch"]

    Path(state_yaml_path).write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
    print(f"seeded: {state_yaml_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
