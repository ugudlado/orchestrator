#!/usr/bin/env bash
# seed-state.sh — Canonical-minimum state.yaml seeder for /orchestrate skill
#
# Usage: seed-state.sh <slug> <schema> [flag=value ...]
#
# Writes $WORKFLOW_STATE_DIR/<slug>/state.yaml and runs generate_plan to produce
# plan.yaml next to it. Idempotent: exits 0 without overwriting an existing state.yaml.
#
# Required environment:
#   REPO_ROOT            — root of the target git repo (default: git rev-parse --show-toplevel)
#   WORKFLOW_STATE_DIR   — directory that holds per-feature state dirs (default: $REPO_ROOT/spec/changes)
#   ORCHESTRATOR_HOME    — path to orchestrator config (default: $HOME/.config/orchestrator)
#
# Exit codes:
#   0 — state.yaml (and plan.yaml) exist in $WORKFLOW_STATE_DIR/<slug>/
#   1 — pre-condition failure (missing project.yaml, schema, flags.yaml, etc.)
#   2 — generate_plan failed
#
# spec.md: FR-1..FR-4
# design.md: Low-Level Design § Components

set -euo pipefail

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <slug> <schema> [flag=value ...]" >&2
    exit 1
fi

SLUG="$1"
SCHEMA="$2"
shift 2
# Remaining positional args are flag overrides: key=value pairs
# (passed to OVERRIDE_ARGS_JSON builder below via "$@")

# ---------------------------------------------------------------------------
# Environment resolution
# ---------------------------------------------------------------------------

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "$REPO_ROOT" ]]; then
    echo "error: REPO_ROOT is not set and git rev-parse failed — cannot locate repo root" >&2
    exit 1
fi

WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"

# ---------------------------------------------------------------------------
# Idempotency: skip if state.yaml already exists (FR-3)
# ---------------------------------------------------------------------------

STATE_DIR="$WORKFLOW_STATE_DIR/$SLUG"
STATE_YAML="$STATE_DIR/state.yaml"

if [[ -f "$STATE_YAML" ]]; then
    echo "state.yaml exists at $STATE_YAML; not overwriting (idempotent skip)" >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Pre-condition: spec/project.yaml must exist (FR-4)
# ---------------------------------------------------------------------------

PROJECT_YAML="$REPO_ROOT/spec/project.yaml"
if [[ ! -f "$PROJECT_YAML" ]]; then
    echo "error: spec/project.yaml not found at $PROJECT_YAML — cannot seed state" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Pre-condition: schema YAML must resolve (FR-4)
# ---------------------------------------------------------------------------

SCHEMA_YAML="$ORCHESTRATOR_HOME/config/workflows/$SCHEMA.yaml"
# Repo override takes precedence
REPO_SCHEMA_OVERRIDE="$REPO_ROOT/.orchestrator/workflows/$SCHEMA.yaml"
if [[ -f "$REPO_SCHEMA_OVERRIDE" ]]; then
    SCHEMA_YAML="$REPO_SCHEMA_OVERRIDE"
fi

if [[ ! -f "$SCHEMA_YAML" ]]; then
    echo "error: schema '$SCHEMA' not found. Searched: $SCHEMA_YAML" >&2
    exit 1
fi

FLAGS_YAML="$ORCHESTRATOR_HOME/config/flags.yaml"
if [[ ! -f "$FLAGS_YAML" ]]; then
    echo "error: flags.yaml not found at $FLAGS_YAML" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Delegate state.yaml construction to Python (YAML r/w — can't do in pure bash)
# ---------------------------------------------------------------------------

mkdir -p "$STATE_DIR"

# Build flag overrides JSON string from "key=value" args
# e.g. ["auto=true", "tdd_required=false"] → {"auto": true, "tdd_required": false}
OVERRIDE_ARGS_JSON=$(python3 - "$@" <<'PYEOF'
import sys
import json

overrides = {}
for arg in sys.argv[1:]:
    if "=" not in arg:
        print(f"error: flag override '{arg}' must be in key=value format", file=sys.stderr)
        sys.exit(1)
    k, v = arg.split("=", 1)
    if v.lower() == "true":
        overrides[k] = True
    elif v.lower() == "false":
        overrides[k] = False
    else:
        overrides[k] = v
print(json.dumps(overrides))
PYEOF
) || exit 1

python3 - \
    "$SLUG" \
    "$SCHEMA" \
    "$STATE_YAML" \
    "$REPO_ROOT" \
    "$SCHEMA_YAML" \
    "$FLAGS_YAML" \
    "$OVERRIDE_ARGS_JSON" \
    <<'PYEOF'
"""
Inline Python: reads schema + flags, computes workflow_plan, writes state.yaml.
Called from seed-state.sh; arguments are positional (no argparse).
"""
import sys
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

slug            = sys.argv[1]
schema_name     = sys.argv[2]
state_yaml_path = sys.argv[3]
repo_root       = sys.argv[4]
schema_yaml_path = sys.argv[5]
flags_yaml_path = sys.argv[6]
override_json   = sys.argv[7]

flag_overrides = json.loads(override_json)

# Load schema
with open(schema_yaml_path, "r") as f:
    schema = yaml.safe_load(f)

# Load flags.yaml
with open(flags_yaml_path, "r") as f:
    flags_def = yaml.safe_load(f)

# Merge flags: gates defaults + behavioral defaults + CLI overrides
flags: dict = {}
for fname, fdata in (flags_def.get("gates") or {}).items():
    flags[fname] = fdata.get("default", False)
for fname, fdata in (flags_def.get("behavioral") or {}).items():
    flags[fname] = fdata.get("default", False)

# Apply overrides
for k, v in flag_overrides.items():
    # Validate override keys exist in flags.yaml (fail-loud per design.md)
    known = set((flags_def.get("gates") or {}).keys()) | set((flags_def.get("behavioral") or {}).keys())
    if k not in known:
        print(f"error: unknown flag override '{k}' — not listed in flags.yaml", file=sys.stderr)
        sys.exit(1)
    flags[k] = v

# Compute workflow_plan from schema steps + gate-flag filter (mirrors workflow-init rule)
# bugfix.yaml and similar flat schemas have a top-level `steps:` list → synthesize `main` phase.
gates = flags_def.get("gates") or {}

raw_steps = schema.get("steps", [])
# Flatten: steps may be strings or dicts with {id, ...}
active = []
filtered = []

for step_entry in raw_steps:
    if isinstance(step_entry, dict):
        step_id = step_entry.get("id", "")
    else:
        step_id = str(step_entry)

    # Check every gate flag that references this step
    blocking_flags = [
        fname
        for fname, fdata in gates.items()
        if step_id in (fdata.get("steps") or [])
    ]
    step_active = all(flags.get(f, False) for f in blocking_flags)

    if step_active:
        active.append(step_id)
    else:
        reason_flag = next(f for f in blocking_flags if not flags.get(f, False))
        filtered.append({"id": step_id, "reason": f"flag {reason_flag}=false"})

workflow_plan = {
    "main": {
        "active": active,
        "filtered": filtered,
    }
}

# First active step determines next_step
if not active:
    print("error: no active steps found after gate-flag filtering — cannot seed state", file=sys.stderr)
    sys.exit(1)

first_step = active[0]

# Write state.yaml (FR-1 canonical minimum field set)
state = {
    "change_id": slug,
    "slug": slug,
    "schema": schema_name,
    "status": "active",
    "repo_root": repo_root,
    "flags": flags,
    "workflow_plan": workflow_plan,
    "phase": "main",
    "next_step": {
        "phase": "main",
        "step_id": first_step,
    },
    "step_history": [],
}
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
state["created_at"] = now_iso
state["started_at"] = now_iso

with open(state_yaml_path, "w") as f:
    yaml.safe_dump(state, f, sort_keys=False, allow_unicode=True)

print(f"seeded: {state_yaml_path}", file=sys.stderr)
PYEOF

PYTHON_EXIT=$?
if [[ $PYTHON_EXIT -ne 0 ]]; then
    # Clean up partial state.yaml so idempotency guard doesn't block future runs
    rm -f "$STATE_YAML"
    exit 1
fi

# ---------------------------------------------------------------------------
# Generate plan.yaml (FR-2)
# ---------------------------------------------------------------------------

# Find the python executable with orchestrator_next importable
# The CLI bin/orchestrator uses python3 from PATH; we do the same.
# The module is in config/scripts/ relative to REPO_ROOT.
SCRIPTS_DIR="$REPO_ROOT/config/scripts"

PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$SCRIPTS_DIR" \
    python3 -m orchestrator_next.generate_plan "$STATE_YAML"
GENPLAN_EXIT=$?

if [[ $GENPLAN_EXIT -ne 0 ]]; then
    echo "error: generate_plan failed (exit $GENPLAN_EXIT) — removing partial state.yaml" >&2
    rm -f "$STATE_YAML"
    exit 2
fi

PLAN_YAML="${STATE_YAML%state.yaml}plan.yaml"
if [[ ! -f "$PLAN_YAML" ]]; then
    echo "error: generate_plan exited 0 but plan.yaml was not created at $PLAN_YAML" >&2
    rm -f "$STATE_YAML"
    exit 2
fi

echo "seeded: $PLAN_YAML" >&2
echo "seed-state: $SLUG ($SCHEMA) ready at $STATE_DIR" >&2
