#!/usr/bin/env bash
# check-bootstrap-state.sh — read .tooling-state.json; report whether bootstrap
# has been run. Bootstrap-only step (schema out of HL-287 scope); kept as a
# thin shim so the bootstrap workflow schema resolves.
#
# Env inputs:  REPO_ROOT
# Outputs:     {bootstrapped: bool, state: {...}}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
TOOLING_STATE="$REPO_ROOT/.tooling-state.json"

if [ ! -f "$TOOLING_STATE" ]; then
  printf '%s\n' '{"bootstrapped": false, "state": null}'
  exit 0
fi

python3 <<PY
import json
with open("$TOOLING_STATE") as f:
    try:
        state = json.load(f)
    except Exception:
        state = None
print(json.dumps({
    "bootstrapped": state is not None,
    "state": state,
}))
PY
