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

# HL-291: upsert feature_complexity row — errors are swallowed so DB contention
# or a missing metrics.duckdb never blocks change completion (|| true pattern).
python3 - <<'PY' || true
import sys, os, yaml
STATE_PATH = os.environ.get("STATE_YAML_PATH", "")
ORCHESTRATOR_HOME = os.environ.get("ORCHESTRATOR_HOME", "")
METRICS_DB = os.environ.get("METRICS_DB") or (
    os.path.join(ORCHESTRATOR_HOME, "metrics.duckdb") if ORCHESTRATOR_HOME else ""
)
if not STATE_PATH or not METRICS_DB:
    sys.exit(0)
sys.path.insert(0, os.path.join(ORCHESTRATOR_HOME, "config", "scripts"))
import duckdb
from orchestrator_next.upsert import ensure_schema, upsert_feature_complexity
with open(STATE_PATH) as f:
    state = yaml.safe_load(f) or {}
conn = duckdb.connect(METRICS_DB)
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
PY
