#!/usr/bin/env bash
# seed-state.sh — Pre-dispatch workflow initializer for /orchestrate skill
#
# Usage: seed-state.sh <slug> <schema> [flag=value ...]
#
# State file: $REPO_ROOT/.orchestrator/<slug>/<timestamp>_<schema>_state.yaml
# Idempotent: exits 0 if a *_<schema>_state.yaml already exists for this slug.
# Worktree creation is NOT done here — the create-worktree step handles it.
# generate_plan promotes the seeded workflow_plan into the nodes shape
# in place — there is no separate plan file (ORC-63).
#
# Required environment:
#   REPO_ROOT            — root of the target git repo (default: git rev-parse --show-toplevel)
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

PROJECT_YAML="$REPO_ROOT/spec/project.yaml"
[[ -f "$PROJECT_YAML" ]] || { echo "error: spec/project.yaml not found at $PROJECT_YAML" >&2; exit 1; }

SCHEMA_YAML="$ORCHESTRATOR_HOME/config/workflows/$SCHEMA.yaml"
REPO_SCHEMA_OVERRIDE="$REPO_ROOT/.orchestrator/workflows/$SCHEMA.yaml"
[[ -f "$REPO_SCHEMA_OVERRIDE" ]] && SCHEMA_YAML="$REPO_SCHEMA_OVERRIDE"
[[ -f "$SCHEMA_YAML" ]] || { echo "error: schema '$SCHEMA' not found. Searched: $SCHEMA_YAML" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Parse overrides and resolve active steps from the schema.
# ---------------------------------------------------------------------------

SCRIPTS_LIB="$(dirname "$0")/lib"
INIT_JSON=$(python3 "$SCRIPTS_LIB/seed_parse_overrides.py" \
    "$SLUG" "$SCHEMA" "$REPO_ROOT" \
    "$SCHEMA_YAML" \
    "$@") || exit 1

# ---------------------------------------------------------------------------
# Resolve STATE_DIR — active state lives in $REPO_ROOT/.orchestrator/<slug>/
# ---------------------------------------------------------------------------

STATE_DIR="$REPO_ROOT/.orchestrator/$SLUG"

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

python3 "$SCRIPTS_LIB/seed_write_state.py" "$STATE_YAML" "$INIT_JSON" "$PRIOR" \
    || { rm -f "$STATE_YAML"; exit 1; }

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
NODES_OK=$(python3 -c "
import yaml; state = yaml.safe_load(open('$STATE_YAML').read()) or {}
nodes = (((state.get('workflow_plan') or {}).get('main') or {}).get('nodes'))
print('yes' if isinstance(nodes, list) and nodes else 'no')
")
if [[ "$NODES_OK" != "yes" ]]; then
    echo "error: generate_plan exited 0 but workflow_plan.main.nodes is empty/absent in $STATE_YAML" >&2
    rm -f "$STATE_YAML"; exit 2
fi

echo "init-workflow: $SLUG ($SCHEMA) ready at $STATE_YAML" >&2
# Print the resolved path so callers can capture it.
echo "$STATE_YAML"
