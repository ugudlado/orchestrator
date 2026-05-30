#!/usr/bin/env bash
# cost-report — emit feature cost markdown + one-line tail before archive.
#
# Env: ORCHESTRATOR_STATE_YAML_PATH, ORCHESTRATOR_REPO_ROOT, ORCHESTRATOR_HOME
# Writes: <state-dir>/cost-summary.md
# Outputs: tail_summary, cost_summary_path (relative to repo/worktree root)
set -uo pipefail

STATE="${ORCHESTRATOR_STATE_YAML_PATH:-}"
REPO_ROOT="${ORCHESTRATOR_REPO_ROOT:-$(pwd)}"

if [ -z "$STATE" ] || [ ! -f "$STATE" ]; then
  echo "cost-report: missing ORCHESTRATOR_STATE_YAML_PATH" >&2
  printf '%s\n' '{"status": "failed", "evidence": {"summary": "missing state.yaml"}}'
  exit 1
fi

CHANGE_DIR="$(cd "$(dirname "$STATE")" && pwd)"
_ORCH_HOME="${ORCHESTRATOR_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
COST_SH=""
for candidate in \
  "$_ORCH_HOME/orchestrator_next/scripts/metrics/cost-report.sh" \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator_next/scripts/metrics" && pwd)/cost-report.sh"; do
  if [ -f "$candidate" ]; then
    COST_SH="$candidate"
    break
  fi
done

if [ -z "$COST_SH" ]; then
  echo "cost-report: metrics/cost-report.sh not found" >&2
  printf '%s\n' '{"status": "failed", "evidence": {"summary": "cost-report.sh missing"}}'
  exit 1
fi

CHANGE_ID="$(python3 - "$STATE" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f) or {}
print(d.get("change_id") or d.get("slug") or "")
PY
)"

if [ -z "$CHANGE_ID" ]; then
  echo "cost-report: change_id missing in state.yaml" >&2
  printf '%s\n' '{"status": "failed", "evidence": {"summary": "missing change_id"}}'
  exit 1
fi

export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$_ORCH_HOME}"
export REPO_ROOT

SUMMARY_PATH="$CHANGE_DIR/cost-summary.md"
if ! bash "$COST_SH" --change-id "$CHANGE_ID" > "$SUMMARY_PATH" 2>"$CHANGE_DIR/.cost-report.err"; then
  echo "cost-report: full report failed (see .cost-report.err)" >&2
  cat "$CHANGE_DIR/.cost-report.err" >&2 2>/dev/null || true
  printf '%s\n' '{"status": "failed", "evidence": {"summary": "cost-report.sh exited non-zero"}}'
  exit 1
fi
rm -f "$CHANGE_DIR/.cost-report.err"

TAIL_LINE="$(bash "$COST_SH" --change-id "$CHANGE_ID" --tail 2>/dev/null || true)"
if [ -n "$TAIL_LINE" ]; then
  echo "cost-report: $TAIL_LINE" >&2
fi

REL_PATH="$(python3 - "$STATE" "$SUMMARY_PATH" <<'PY'
import os, sys
state, summary = sys.argv[1], sys.argv[2]
with open(state) as f:
    import yaml
    d = yaml.safe_load(f) or {}
root = d.get("worktree_path") or d.get("repo_root") or ""
if root:
    try:
        print(os.path.relpath(summary, root))
    except ValueError:
        print(summary)
else:
    print(summary)
PY
)"

printf '%s\n' "$(python3 - "$TAIL_LINE" "$REL_PATH" <<'PY'
import json, sys
tail, path = sys.argv[1], sys.argv[2]
print(json.dumps({
    "status": "completed",
    "outputs": {
        "tail_summary": tail,
        "cost_summary_path": path,
    },
}))
PY
)"
