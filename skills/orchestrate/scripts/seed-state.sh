#!/usr/bin/env bash
# seed-state.sh — Pre-dispatch workflow initializer for /orchestrate skill
#
# Usage: seed-state.sh <slug> <schema> [flag=value ...]
#
# When flags.worktree=true: creates the git worktree first, then writes
# state.yaml under $WORKTREE_PATH/spec/changes/<slug>/.
# Otherwise writes under $WORKFLOW_STATE_DIR/<slug>/ (repo_root).
# Idempotent: exits 0 without overwriting an existing state.yaml.
# generate_plan promotes the seeded workflow_plan into the nodes shape
# in place inside state.yaml — there is no separate plan file (ORC-63).
#
# Required environment:
#   REPO_ROOT            — root of the target git repo (default: git rev-parse --show-toplevel)
#   WORKFLOW_STATE_DIR   — fallback state dir when worktree=false (default: $REPO_ROOT/spec/changes)
#   ORCHESTRATOR_HOME    — path to orchestrator config (default: $HOME/.config/orchestrator)
#
# Exit codes:
#   0 — state.yaml exists with a promoted workflow_plan in the resolved state dir
#   1 — pre-condition failure
#   2 — generate_plan failed
#
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <slug> <schema> [flag=value ...]" >&2
    exit 1
fi

SLUG="$1"; SCHEMA="$2"; shift 2

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
[[ -n "$REPO_ROOT" ]] || { echo "error: cannot locate repo root" >&2; exit 1; }

WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
WORKTREE_BASE_DIR="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"

PROJECT_YAML="$REPO_ROOT/spec/project.yaml"
[[ -f "$PROJECT_YAML" ]] || { echo "error: spec/project.yaml not found at $PROJECT_YAML" >&2; exit 1; }

SCHEMA_YAML="$ORCHESTRATOR_HOME/config/workflows/$SCHEMA.yaml"
REPO_SCHEMA_OVERRIDE="$REPO_ROOT/.orchestrator/workflows/$SCHEMA.yaml"
[[ -f "$REPO_SCHEMA_OVERRIDE" ]] && SCHEMA_YAML="$REPO_SCHEMA_OVERRIDE"
[[ -f "$SCHEMA_YAML" ]] || { echo "error: schema '$SCHEMA' not found. Searched: $SCHEMA_YAML" >&2; exit 1; }

# ORC-105: flags merged into config/workflow.yaml (gates/behavioral/cli keys
# unchanged at top level); legacy config/flags.yaml fallback.
FLAGS_YAML="$ORCHESTRATOR_HOME/config/workflow.yaml"
[[ -f "$FLAGS_YAML" ]] || FLAGS_YAML="$ORCHESTRATOR_HOME/config/flags.yaml"
[[ -f "$FLAGS_YAML" ]] || { echo "error: workflow.yaml/flags.yaml not found in $ORCHESTRATOR_HOME/config/" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Single Python pass: parse overrides, resolve worktree flag, emit state.yaml
# Outputs two lines to stdout: "worktree=<true|false>" then "state_yaml=<path>"
# The state_yaml path is provisional (worktree_path patched in after git ops).
# ---------------------------------------------------------------------------

INIT_JSON=$(python3 - \
    "$SLUG" "$SCHEMA" "$REPO_ROOT" \
    "$SCHEMA_YAML" "$FLAGS_YAML" \
    "$WORKFLOW_STATE_DIR" \
    "$WORKTREE_BASE_DIR" \
    "$@" \
    <<'PYEOF'
import sys, json
from datetime import datetime, timezone
from pathlib import Path
import yaml

slug, schema_name, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]
schema_yaml_path, flags_yaml_path = sys.argv[4], sys.argv[5]
workflow_state_dir, worktree_base_dir = sys.argv[6], sys.argv[7]
raw_overrides = sys.argv[8:]

# Parse key=value overrides
overrides: dict = {}
for arg in raw_overrides:
    if "=" not in arg:
        print(f"error: flag override '{arg}' must be in key=value format", file=sys.stderr)
        sys.exit(1)
    k, v = arg.split("=", 1)
    overrides[k] = True if v.lower() == "true" else (False if v.lower() == "false" else v)

schema = yaml.safe_load(Path(schema_yaml_path).read_text())
flags_def = yaml.safe_load(Path(flags_yaml_path).read_text()) or {}

# Merge flags: gate defaults + behavioral defaults + CLI overrides
flags: dict = {}
for section in ("gates", "behavioral"):
    for fname, fdata in (flags_def.get(section) or {}).items():
        flags[fname] = fdata.get("default", False)

known = set(flags.keys())
for k, v in overrides.items():
    if k not in known:
        print(f"error: unknown flag override '{k}' — not listed in flags.yaml", file=sys.stderr)
        sys.exit(1)
    flags[k] = v

# Build workflow_plan via gate-flag filter
gates = flags_def.get("gates") or {}
active, filtered = [], []
for step_entry in schema.get("steps", []):
    step_id = step_entry.get("id", "") if isinstance(step_entry, dict) else str(step_entry)
    blocking = [f for f, fd in gates.items() if step_id in (fd.get("steps") or [])]
    if all(flags.get(f, False) for f in blocking):
        active.append(step_id)
    else:
        reason = next(f for f in blocking if not flags.get(f, False))
        filtered.append({"id": step_id, "reason": f"flag {reason}=false"})

if not active:
    print("error: no active steps after gate-flag filtering", file=sys.stderr)
    sys.exit(1)

# Emit JSON consumed by bash for worktree setup + deferred state write
print(json.dumps({
    "worktree": bool(flags.get("worktree", False)),
    "slug": slug,
    "schema_name": schema_name,
    "repo_root": repo_root,
    "workflow_state_dir": workflow_state_dir,
    "worktree_base_dir": worktree_base_dir,
    "flags": flags,
    "active": active,
    "filtered": filtered,
}))
PYEOF
) || exit 1

USE_WORKTREE=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read())['worktree'])" <<< "$INIT_JSON")

# ---------------------------------------------------------------------------
# Worktree creation (before any file writes so STATE_DIR resolves correctly)
# ---------------------------------------------------------------------------

BRANCH="$SCHEMA/$SLUG"
WORKTREE_PATH=""

if [[ "$USE_WORKTREE" == "True" ]]; then
    WORKTREE_PATH="$WORKTREE_BASE_DIR/$SLUG"
    mkdir -p "$WORKTREE_BASE_DIR"

    if git -C "$REPO_ROOT" worktree list --porcelain | grep -q "worktree $WORKTREE_PATH"; then
        echo "worktree already exists at $WORKTREE_PATH" >&2
    else
        git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WORKTREE_PATH" HEAD 2>&1 >&2 || {
            git -C "$REPO_ROOT" worktree add "$WORKTREE_PATH" "$BRANCH" 2>&1 >&2 || {
                echo "error: git worktree add failed" >&2; exit 1
            }
        }
    fi
fi

# ---------------------------------------------------------------------------
# Resolve STATE_DIR and write state.yaml
# ---------------------------------------------------------------------------

if [[ -n "$WORKTREE_PATH" ]]; then
    STATE_DIR="$WORKTREE_PATH/spec/changes/$SLUG"
else
    STATE_DIR="$WORKFLOW_STATE_DIR/$SLUG"
fi
STATE_YAML="$STATE_DIR/state.yaml"

if [[ -f "$STATE_YAML" ]]; then
    echo "state.yaml exists at $STATE_YAML; not overwriting (idempotent skip)" >&2
    exit 0
fi

mkdir -p "$STATE_DIR"

python3 - "$STATE_YAML" "$WORKTREE_PATH" "$BRANCH" "$INIT_JSON" <<'PYEOF'
import sys, json
from datetime import datetime, timezone
from pathlib import Path
import yaml

state_yaml_path, worktree_path, branch = sys.argv[1], sys.argv[2] or None, sys.argv[3] or None
d = json.loads(sys.argv[4])

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
state = {
    "change_id": d["slug"], "slug": d["slug"], "schema": d["schema_name"],
    "status": "active", "repo_root": d["repo_root"], "flags": d["flags"],
    "workflow_plan": {"main": {"active": d["active"], "filtered": d["filtered"]}},
    "phase": "main", "next_step": {"phase": "main", "step_id": d["active"][0]},
    "step_history": [], "created_at": now, "started_at": now,
    "project_context_loaded": True,
}
if worktree_path:
    state["worktree_path"] = worktree_path
if branch:
    state["branch"] = branch

Path(state_yaml_path).write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
print(f"seeded: {state_yaml_path}", file=sys.stderr)
PYEOF

[[ $? -eq 0 ]] || { rm -f "$STATE_YAML"; exit 1; }

# ---------------------------------------------------------------------------
# generate_plan — promotes the seeded workflow_plan into the nodes shape
# in place inside state.yaml (no separate file is written).
# ---------------------------------------------------------------------------

PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT/config/scripts" \
    python3 -m orchestrator_next.generate_plan "$STATE_YAML"
GENPLAN_EXIT=$?

if [[ $GENPLAN_EXIT -ne 0 ]]; then
    echo "error: generate_plan failed (exit $GENPLAN_EXIT) — removing partial state.yaml" >&2
    rm -f "$STATE_YAML"; exit 2
fi

# Verify the promotion produced a non-empty workflow_plan.main.nodes list.
NODES_OK=$(python3 - "$STATE_YAML" <<'PYEOF'
import sys, yaml
state = yaml.safe_load(open(sys.argv[1]).read()) or {}
nodes = (((state.get("workflow_plan") or {}).get("main") or {}).get("nodes"))
print("yes" if isinstance(nodes, list) and nodes else "no")
PYEOF
)
if [[ "$NODES_OK" != "yes" ]]; then
    echo "error: generate_plan exited 0 but workflow_plan.main.nodes is empty/absent in $STATE_YAML" >&2
    rm -f "$STATE_YAML"; exit 2
fi

echo "init-workflow: $SLUG ($SCHEMA) ready at $STATE_DIR" >&2
