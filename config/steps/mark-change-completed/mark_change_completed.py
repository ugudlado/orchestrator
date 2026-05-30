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


def _upsert_complexity(state_path: Path) -> None:
    orch_home = os.environ.get("ORCHESTRATOR_HOME", "")
    metrics_db = os.environ.get("METRICS_DB") or (
        os.path.join(orch_home, "metrics.duckdb") if orch_home else ""
    )
    if not orch_home or not metrics_db:
        return
    sys.path.insert(0, orch_home)
    import duckdb

    from orchestrator_next.upsert import ensure_schema, upsert_feature_complexity

    with state_path.open(encoding="utf-8") as f:
        state = yaml.safe_load(f) or {}
    conn = duckdb.connect(metrics_db)
    ensure_schema(conn)
    upsert_feature_complexity(
        conn,
        repo_root=str(state.get("repo_root") or ""),
        change_id=str(state.get("change_id") or ""),
        complexity=state.get("complexity"),
        schema_name=str(state.get("schema") or ""),
        started_at=state.get("created_at"),
        completed_at=state.get("completed_at"),
    )
    conn.close()


def main() -> int:
    state_path = os.environ.get("STATE_YAML_PATH", "")
    if not state_path or not Path(state_path).is_file():
        print(json.dumps({"error": "STATE_YAML_PATH must point to existing state.yaml"}))
        return 3
    if not os.environ.get("ORCHESTRATOR_HOME"):
        print("error: ORCHESTRATOR_HOME required", file=sys.stderr)
        return 3

    path = Path(state_path)
    result = _stamp_state(path)
    print(json.dumps(result))
    try:
        _upsert_complexity(path)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
