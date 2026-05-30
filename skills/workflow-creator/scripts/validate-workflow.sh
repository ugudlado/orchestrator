#!/usr/bin/env bash
# validate-workflow.sh — smoke-check a workflow schema and its step contracts.
#
# Usage: validate-workflow.sh <schema-name>
# Exit 0 when workflow exists, every step has agent: or run:, generate_plan succeeds.
set -euo pipefail

SCHEMA="${1:-}"
if [ -z "$SCHEMA" ]; then
  echo "Usage: $0 <schema-name>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WF="$REPO_ROOT/config/workflows/${SCHEMA}.yaml"
STEPS_DIR="$REPO_ROOT/config/steps"

if [ ! -f "$WF" ]; then
  echo "ERROR: workflow not found: $WF" >&2
  exit 1
fi

echo "Checking workflow: $WF" >&2

missing=()
violations=()
while IFS= read -r step_id; do
  [ -n "$step_id" ] || continue
  contract="$STEPS_DIR/$step_id/contract.yaml"
  flat="$STEPS_DIR/${step_id}.yaml"
  if [ ! -f "$contract" ] && [ ! -f "$flat" ]; then
    missing+=("$step_id")
    continue
  fi
  path="$contract"
  [ -f "$path" ] || path="$flat"
  if ! python3 - "$path" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
if not (data.get("agent") or data.get("run")):
    raise SystemExit(1)
PY
  then
    violations+=("$step_id")
  fi
done < <(python3 - "$WF" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
for entry in data.get("steps") or []:
    if isinstance(entry, dict):
        print(entry.get("id", ""))
    else:
        print(str(entry).split("#")[0].strip())
PY
)

if [ "${#missing[@]}" -gt 0 ]; then
  echo "ERROR: missing contracts:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi
if [ "${#violations[@]}" -gt 0 ]; then
  echo "ERROR: contracts missing agent: and run:" >&2
  printf '  - %s\n' "${violations[@]}" >&2
  exit 1
fi

# generate_plan smoke (skip operator/resume-only schemas without a standard seed shape)
case "$SCHEMA" in
  telemetry|complete)
    echo "OK: contracts valid ($SCHEMA — generate_plan smoke skipped)" >&2
    exit 0
    ;;
esac

export ORCHESTRATOR_HOME="$REPO_ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STATE="$TMP/state.yaml"
python3 - "$STATE" "$SCHEMA" "$REPO_ROOT" <<'PY'
import sys, yaml
from datetime import datetime, timezone
state_path, schema, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
state = {
    "change_id": "wf-validate",
    "slug": "wf-validate",
    "schema": schema,
    "status": "active",
    "repo_root": repo_root,
    "flags": {},
    "workflow_plan": {"main": {"active": [], "filtered": []}},
    "phase": "main",
    "step_history": [],
    "created_at": now,
}
wf = yaml.safe_load(open(f"{repo_root}/config/workflows/{schema}.yaml"))
state["workflow_plan"]["main"]["active"] = [
    (e.get("id") if isinstance(e, dict) else str(e).split("#")[0].strip())
    for e in wf.get("steps", [])
]
yaml.safe_dump(state, open(state_path, "w"), sort_keys=False)
PY

PYTHONPATH="$REPO_ROOT" python3 -m orchestrator_next.generate_plan "$STATE" >/dev/null
echo "OK: $SCHEMA — contracts valid, generate_plan succeeded" >&2
