#!/usr/bin/env bash
# mark-change-completed.sh — stamp state.yaml with completion fields.
#
# Env inputs:  STATE_YAML_PATH (required; absolute path)
# Outputs:     {status, completed_at, archive_path}

set -uo pipefail

STATE="${STATE_YAML_PATH:-}"
if [ -z "$STATE" ] || [ ! -f "$STATE" ]; then
  printf '%s\n' '{"error": "STATE_YAML_PATH env var must point to existing state.yaml"}'
  exit 3
fi

python3 <<PY
import json, yaml, datetime as dt
STATE = "$STATE"
with open(STATE) as f:
    d = yaml.safe_load(f) or {}
if d.get("status") == "completed" and d.get("completed_at"):
    print(json.dumps({
        "status": "completed",
        "completed_at": d["completed_at"],
        "archive_path": d.get("archive_path", ""),
    }))
    raise SystemExit(0)
now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
date_prefix = now[:10]
cid = d.get("change_id") or d.get("linear_ticket_id") or "unknown"
archive_path = f"spec/changes/archive/{date_prefix}-{cid}/"
d["status"] = "completed"
d["completed_at"] = now
d["archive_path"] = archive_path
with open(STATE, "w") as f:
    yaml.safe_dump(d, f, sort_keys=False, default_flow_style=False)
print(json.dumps({
    "status": "completed",
    "completed_at": now,
    "archive_path": archive_path,
}))
PY
