#!/usr/bin/env bash
# ticket-state-update.sh — merge ticket_* fields into state.yaml (shell loop only)
#
# Usage: ticket-state-update.sh <state.yaml> <<'JSON'
#   { "ticket_id": "...", "ticket_status": "In Progress", "ticket_rework": true,
#     "flags": { "rework_from_review": true } }
#
# Only whitelisted keys are written. Does not touch step_history or next_step.
set -euo pipefail

STATE_YAML="${1:-}"

if [ -z "$STATE_YAML" ] || [ ! -f "$STATE_YAML" ]; then
  echo "Usage: ticket-state-update.sh <state.yaml> (JSON on stdin)" >&2
  exit 1
fi

STATE_YAML=$(cd "$(dirname "$STATE_YAML")" && pwd)/$(basename "$STATE_YAML")

PATCH_JSON=$(cat)
if [ -z "$PATCH_JSON" ]; then
  echo "ticket-state-update: empty JSON on stdin" >&2
  exit 1
fi

python3 - "$STATE_YAML" "$PATCH_JSON" <<'PY'
import json, sys
from datetime import datetime, timezone
import yaml

path, patch_raw = sys.argv[1], sys.argv[2]
patch = json.loads(patch_raw)

ALLOWED = {
    "ticket_id", "ticket_status", "ticket_status_checked_at",
    "ticket_rework", "ticketing",
}
FLAG_KEYS = {"rework_from_review"}

with open(path) as f:
    state = yaml.safe_load(f) or {}

for key in ALLOWED:
    if key in patch and patch[key] is not None:
        state[key] = patch[key]

if "ticket_status" in patch and "ticket_status_checked_at" not in patch:
    state["ticket_status_checked_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

flags_patch = patch.get("flags")
if isinstance(flags_patch, dict):
    flags = state.get("flags") or {}
    if not isinstance(flags, dict):
        flags = {}
    for fk, fv in flags_patch.items():
        if fk in FLAG_KEYS:
            flags[fk] = fv
    state["flags"] = flags

with open(path, "w") as f:
    yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
PY
