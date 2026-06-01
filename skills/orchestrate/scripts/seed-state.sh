#!/usr/bin/env bash
# seed-state.sh — Pre-dispatch workflow initializer for /orchestrate skill
#
# Usage: seed-state.sh <slug> <schema> [flag=value ...]
#
# State file: ~/.config/orchestrator/<repo>/<slug>/<timestamp>_<schema>_state.yaml
# Idempotent: exits 0 if a *_<schema>_state.yaml already exists for this slug.
# Context seeding: copies worktree_path, branch, repo_root, change_id, slug
# from the most recent *_state.yaml in the slug dir (any schema), so successive
# workflows (feature → complete) share the same worktree automatically.
# generate_plan promotes the seeded workflow_plan into the nodes shape
# in place — there is no separate plan file (ORC-63).
#
# Required environment:
#   REPO_ROOT            — root of the target git repo (default: git rev-parse --show-toplevel)
#   WORKTREE_BASE_DIR    — parent dir for worktrees (default: $HOME/code/feature_worktrees)
#   ORCHESTRATOR_HOME    — path to orchestrator config (default: $HOME/.config/orchestrator)
#
# Exit codes:
#   0 — <timestamp>_<schema>_state.yaml exists with a promoted workflow_plan
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

ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
WORKTREE_BASE_DIR="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"

PROJECT_YAML="$REPO_ROOT/spec/project.yaml"
[[ -f "$PROJECT_YAML" ]] || { echo "error: spec/project.yaml not found at $PROJECT_YAML" >&2; exit 1; }

SCHEMA_YAML="$ORCHESTRATOR_HOME/config/workflows/$SCHEMA.yaml"
REPO_SCHEMA_OVERRIDE="$REPO_ROOT/.orchestrator/workflows/$SCHEMA.yaml"
[[ -f "$REPO_SCHEMA_OVERRIDE" ]] && SCHEMA_YAML="$REPO_SCHEMA_OVERRIDE"
[[ -f "$SCHEMA_YAML" ]] || { echo "error: schema '$SCHEMA' not found. Searched: $SCHEMA_YAML" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Single Python pass: parse overrides, emit state.yaml init JSON.
# ORC-108: flag registry deleted — the schema's `steps:` list IS the plan
# (no gate-filtering). key=value overrides are persisted to state.flags
# as-is for any schema-specific behavioral reads.
# ---------------------------------------------------------------------------

INIT_JSON=$(python3 - \
    "$SLUG" "$SCHEMA" "$REPO_ROOT" \
    "$SCHEMA_YAML" \
    "$WORKTREE_BASE_DIR" \
    "$@" \
    <<'PYEOF'
import sys, json
from pathlib import Path
import yaml

slug, schema_name, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]
schema_yaml_path = sys.argv[4]
worktree_base_dir = sys.argv[5]
raw_overrides = sys.argv[6:]

# Parse key=value overrides — persisted verbatim to state.flags.
flags: dict = {}
for arg in raw_overrides:
    if "=" not in arg:
        print(f"error: flag override '{arg}' must be in key=value format", file=sys.stderr)
        sys.exit(1)
    k, v = arg.split("=", 1)
    flags[k] = True if v.lower() == "true" else (False if v.lower() == "false" else v)

schema = yaml.safe_load(Path(schema_yaml_path).read_text())

# The steps list IS the plan — no gate-filtering (ORC-108).
active = [
    (step_entry.get("id", "") if isinstance(step_entry, dict) else str(step_entry))
    for step_entry in schema.get("steps", [])
]
if not active:
    print(f"error: schema '{schema_name}' declares no steps", file=sys.stderr)
    sys.exit(1)

# Emit JSON consumed by bash for the deferred state write.
print(json.dumps({
    "slug": slug,
    "schema_name": schema_name,
    "repo_root": repo_root,
    "worktree_base_dir": worktree_base_dir,
    "flags": flags,
    "active": active,
    "filtered": [],
}))
PYEOF
) || exit 1

# ---------------------------------------------------------------------------
# Worktree creation — isolated branch for implementation artifacts.
# ---------------------------------------------------------------------------

BRANCH="$SCHEMA/$SLUG"
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

# ---------------------------------------------------------------------------
# Resolve STATE_DIR — state lives in ~/.config/orchestrator/<repo>/<slug>/,
# independent of the worktree so the engine can always find it.
# ---------------------------------------------------------------------------

REPO_NAME=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null \
    | sed 's/.*[:/]\([^/]*\)\.git$/\1/' \
    | sed 's/.*[:/]\([^/]*\)$/\1/' \
    || basename "$REPO_ROOT")
STATE_DIR="$HOME/.config/orchestrator/$REPO_NAME/$SLUG"

# Idempotency: if a *_<schema>_state.yaml already exists, re-use it.
EXISTING=$(ls "$STATE_DIR"/*_"${SCHEMA}"_state.yaml 2>/dev/null | sort | tail -1 || true)
if [[ -n "$EXISTING" ]]; then
    echo "state file exists at $EXISTING; not overwriting (idempotent skip)" >&2
    echo "$EXISTING"
    exit 0
fi

mkdir -p "$STATE_DIR"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%S)
STATE_YAML="$STATE_DIR/${TIMESTAMP}_${SCHEMA}_state.yaml"

# Seed context from the most recent prior *_state.yaml (any schema), so
# successive workflows share worktree_path, branch, repo_root, etc.
PRIOR=$(ls "$STATE_DIR"/*_state.yaml 2>/dev/null | sort | tail -1 || true)

python3 - "$STATE_YAML" "$WORKTREE_PATH" "$BRANCH" "$INIT_JSON" "$PRIOR" <<'PYEOF'
import sys, json
from datetime import datetime, timezone
from pathlib import Path
import yaml

state_yaml_path, worktree_path, branch = sys.argv[1], sys.argv[2] or None, sys.argv[3] or None
d = json.loads(sys.argv[4])
prior_path = sys.argv[5] if len(sys.argv) > 5 else ""

# Copy identity fields from the most recent prior workflow state if available.
prior_context: dict = {}
if prior_path:
    try:
        prior_raw = yaml.safe_load(Path(prior_path).read_text()) or {}
        for key in ("worktree_path", "branch", "repo_root", "change_id", "slug"):
            if prior_raw.get(key):
                prior_context[key] = prior_raw[key]
    except (OSError, yaml.YAMLError):
        pass  # prior unreadable — start fresh

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
state = {
    "change_id": prior_context.get("change_id") or d["slug"],
    "slug": prior_context.get("slug") or d["slug"],
    "schema": d["schema_name"],
    "status": "active",
    "repo_root": prior_context.get("repo_root") or d["repo_root"],
    "flags": d["flags"],
    "workflow_plan": {"main": {"active": d["active"], "filtered": d["filtered"]}},
    "phase": "main",
    "next_step": {"phase": "main", "step_id": d["active"][0]},
    "step_history": [],
    "created_at": now,
    "started_at": now,
    "project_context_loaded": True,
}
# worktree_path and branch: prior wins, then current run's values.
resolved_worktree = prior_context.get("worktree_path") or worktree_path
resolved_branch = prior_context.get("branch") or branch
if resolved_worktree:
    state["worktree_path"] = resolved_worktree
if resolved_branch:
    state["branch"] = resolved_branch

Path(state_yaml_path).write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
print(f"seeded: {state_yaml_path}", file=sys.stderr)
PYEOF

[[ $? -eq 0 ]] || { rm -f "$STATE_YAML"; exit 1; }

# ---------------------------------------------------------------------------
# generate_plan — promotes the seeded workflow_plan into the nodes shape
# in place inside the state file (no separate file is written).
# ---------------------------------------------------------------------------

PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT" \
    python3 -m orchestrator_next.generate_plan "$STATE_YAML"
GENPLAN_EXIT=$?

if [[ $GENPLAN_EXIT -ne 0 ]]; then
    echo "error: generate_plan failed (exit $GENPLAN_EXIT) — removing partial state file" >&2
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

echo "init-workflow: $SLUG ($SCHEMA) ready at $STATE_YAML" >&2
# Print the resolved path so callers can capture it.
echo "$STATE_YAML"
