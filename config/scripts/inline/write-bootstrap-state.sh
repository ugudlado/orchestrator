#!/usr/bin/env bash
# write-bootstrap-state.sh — Write .tooling-state.json to mark bootstrap complete.
#
# Idempotent: if .tooling-state.json already exists and is valid, skips.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback
#   ORCHESTRATOR_WORKFLOW_DIR — parent dir for state.yaml files

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"
WORKFLOW_DIR="${ORCHESTRATOR_WORKFLOW_DIR:-${WORKFLOW_STATE_DIR:-$REPO/spec/changes}}"

if [ -z "$REPO" ]; then
  echo "[write-bootstrap-state] error: ORCHESTRATOR_REPO_ROOT is not set" >&2
  exit 1
fi

STATE_FILE="$REPO/.tooling-state.json"

# Idempotency: if already written with version:1, skip
if [ -f "$STATE_FILE" ]; then
  if python3 -c "
import json, sys
d = json.load(open('$STATE_FILE'))
sys.exit(0 if d.get('version') == 1 and d.get('checks_passed') else 1)
" 2>/dev/null; then
    echo "[write-bootstrap-state] .tooling-state.json already valid — skipping"
    exit 0
  fi
fi

COMPLETED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
PROJECT_NAME=$(basename "$REPO")

# Write .tooling-state.json
python3 - "$STATE_FILE" "$COMPLETED_AT" <<'PYEOF'
import json, sys, subprocess
from datetime import datetime, timezone

state_file = sys.argv[1]
completed_at = sys.argv[2]

def run_version(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.stdout.strip().split('\n')[0] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"

state = {
    "version": 1,
    "completed_at": completed_at,
    "language": "unknown",
    "package_manager": "unknown",
    "tool_calls": {},
    "checks_passed": True,
}

with open(state_file, "w") as f:
    json.dump(state, f, indent=2)
print(f"[write-bootstrap-state] Wrote {state_file}")
PYEOF

# Write $WORKFLOW_DIR/<slug>/state.yaml
SLUG="${PROJECT_NAME}-bootstrap"
STATE_YAML_DIR="$WORKFLOW_DIR/$SLUG"
mkdir -p "$STATE_YAML_DIR"
STATE_YAML="$STATE_YAML_DIR/state.yaml"

if [ -f "$STATE_YAML" ]; then
  echo "[write-bootstrap-state] $STATE_YAML already exists — skipping state.yaml write"
else
  cat > "$STATE_YAML" << YAMLEOF
schema: bootstrap
status: completed
description: "Bootstrap $PROJECT_NAME repo tooling"
phase: setup
flags:
  portless: false
started_at: "$COMPLETED_AT"
updated_at: "$COMPLETED_AT"
step_history: []
bootstrap:
  language: unknown
  package_manager: unknown
  tools_installed: {}
  gate_results: {}
YAMLEOF
  echo "[write-bootstrap-state] Wrote $STATE_YAML"
fi

echo "[write-bootstrap-state] Complete for $PROJECT_NAME"
echo "  State: .tooling-state.json + $STATE_YAML written"
