#!/usr/bin/env python3
"""check-rerun — the workflow's own decision on whether a finished run reruns.

This is the first step of workflows that want rerun-protection. The engine
does not know what "already completed" means; it just dispatches this step
like any other. The step decides:

  - An archive directory already exists for this change → there is no work to
    do. Mark every plan node completed and stamp status=completed, so the next
    `orchestrator next` finds no ready node and the engine completes the
    workflow (exit 1). Nothing is respawned.
  - No archive directory → complete only this node; the DAG proceeds normally.

Whether a workflow refuses reruns is expressed by whether its schema lists
this step — not by any engine flag or driver gate.

Env inputs (from step_env): STATE_YAML_PATH, REPO_ROOT, CHANGE_ID.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

_DATE_SLUG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def find_archive_dir(repo_root: str, slug: str, ticket_id: str = "") -> str | None:
    """Newest archive dir matching slug (or ticket_id), by dir presence alone.

    Returns the relative archive path (e.g. spec/changes/archive/<dir>/) or None.
    No state.yaml parse — directory existence is the completion signal.
    """
    archive_dir = Path(repo_root) / "spec" / "changes" / "archive"
    if not archive_dir.is_dir():
        return None
    want = _norm(slug) or _norm(ticket_id)
    if not want:
        return None

    matches: list[tuple[str, str]] = []
    for child in archive_dir.iterdir():
        if not child.is_dir():
            continue
        m = _DATE_SLUG_RE.match(child.name)
        date_prefix = m.group(1) if m else ""
        dir_slug = _norm(m.group(2)) if m else _norm(child.name)
        if dir_slug == want:
            matches.append((date_prefix, f"spec/changes/archive/{child.name}/"))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def _complete_node(node: dict[str, Any]) -> None:
    if isinstance(node, dict) and node.get("id"):
        node["status"] = "completed"


def _finalize_completed(state: dict[str, Any], archive_path: str) -> None:
    """Mark every node in every phase completed and stamp top-level completion."""
    for phase_plan in (state.get("workflow_plan") or {}).values():
        if isinstance(phase_plan, dict):
            for node in phase_plan.get("nodes") or []:
                _complete_node(node)
    state["status"] = "completed"
    state["archive_path"] = archive_path
    state["completed_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.pop("next_step", None)


def main() -> int:
    state_path = os.environ.get("STATE_YAML_PATH", "")
    if not state_path or not Path(state_path).is_file():
        print(json.dumps({"error": "STATE_YAML_PATH must point to existing state.yaml"}))
        return 3

    path = Path(state_path)
    state = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    repo_root = os.environ.get("REPO_ROOT") or str(state.get("repo_root") or "")
    slug = os.environ.get("CHANGE_ID") or str(state.get("change_id") or state.get("slug") or "")
    ticket_id = str(state.get("ticket_id") or "")

    archive_path = find_archive_dir(repo_root, slug, ticket_id)
    if archive_path:
        _finalize_completed(state, archive_path)
        path.write_text(yaml.safe_dump(state, sort_keys=False, default_flow_style=False), encoding="utf-8")
        print(json.dumps({
            "rerun": "halted",
            "archive_path": archive_path,
            "message": f"Feature {ticket_id or slug} is already completed (archive: {archive_path}).",
        }))
        return 0

    # No archive → nothing to do here. The engine marks this inline node
    # completed on exit 0 (bin/orchestrator records it), so the DAG advances
    # to the next step on its own — no state write needed.
    print(json.dumps({"rerun": "proceed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
