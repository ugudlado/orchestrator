#!/usr/bin/env python3
"""Stamp state.yaml with completion fields and optionally upsert metrics."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import yaml


def _stamp_state(state_path: Path) -> dict:
    with state_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if data.get("status") == "completed" and data.get("completed_at"):
        return {
            "status": "completed",
            "completed_at": data["completed_at"],
            "archive_path": data.get("archive_path", ""),
        }
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cid = data.get("change_id") or data.get("ticket_id") or "unknown"
    archive_path = f"spec/changes/archive/{cid}/"
    data["status"] = "completed"
    data["completed_at"] = now
    data["archive_path"] = archive_path
    with state_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    return {
        "status": "completed",
        "completed_at": now,
        "archive_path": archive_path,
    }


def main() -> int:
    state_path = os.environ.get("STATE_YAML_PATH", "")
    if not state_path or not Path(state_path).is_file():
        print(json.dumps({"error": "STATE_YAML_PATH must point to existing state.yaml"}))
        return 3
    if not os.environ.get("ORCHESTRATOR_CONFIG"):
        print("error: ORCHESTRATOR_CONFIG required", file=sys.stderr)
        return 3

    path = Path(state_path)
    result = _stamp_state(path)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
