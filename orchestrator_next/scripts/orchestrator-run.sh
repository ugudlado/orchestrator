#!/usr/bin/env bash
# orchestrator-run.sh — ticket-driven entry for the shell workflow loop
#
# Usage:
#   orchestrator run <ticket-id> [--schema feature|bugfix] [--repo PATH] [flag=value ...]
#
# Resolves repo root, consults ticket status, seeds state.yaml when needed,
# then execs run-workflow.sh. Use this instead of seed-state + run-workflow
# when not driving the workflow from chat (/orchestrate).
#
# Exit codes: same as run-workflow.sh (1=complete, 2=blocked, 3–7 errors)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_WORKTREE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_WORKFLOW="$SCRIPT_DIR/run-workflow.sh"
# shellcheck source=lib/agent-routes.sh
source "$SCRIPT_DIR/lib/agent-routes.sh"
SEED_STATE="${ORCHESTRATOR_HOME:-}/skills/orchestrate/scripts/seed-state.sh"
# When invoked from a dev checkout, prefer repo-local seed + scripts
if [ -f "$_WORKTREE_ROOT/skills/orchestrate/scripts/seed-state.sh" ]; then
  SEED_STATE="$_WORKTREE_ROOT/skills/orchestrate/scripts/seed-state.sh"
fi

TICKET_ID=""
SCHEMA="feature"
REPO_ROOT_ARG=""
ROUTES_OVERRIDE_ARG=""
AGENTS_CONFIG_ARG=""

FLAG_OVERRIDES=()
AGENT_ROUTE_FLAGS=()


usage() {
  echo "Usage: orchestrator run <ticket-id> [--schema feature|bugfix|complete|...] [--repo PATH] [--routes-override FILE] [--agents-config FILE] [flag=value ...]" >&2
  echo "  Examples:" >&2
  echo "    orchestrator run ORC-83" >&2
  echo "    orchestrator run HL-287 --schema bugfix" >&2
  echo "    orchestrator run 42 --repo /path/to/app worktree=true" >&2
  echo "    orchestrator run ORC-84 --agents-config ./agents.config.yaml" >&2
  echo "    orchestrator run ORC-84 agents.config=./agents.config.yaml" >&2
  echo "    orchestrator run ORC-84 agent.developer.subprocess=cursor" >&2
  echo "    orchestrator run ORC-84 --routes-override ./my-routes.yaml" >&2
  exit 7
}

while [ $# -gt 0 ]; do
  case "$1" in
    --schema)
      [ $# -ge 2 ] || usage
      SCHEMA="$2"
      shift 2
      ;;
    --repo)
      [ $# -ge 2 ] || usage
      REPO_ROOT_ARG="$2"
      shift 2
      ;;
    --routes-override)
      [ $# -ge 2 ] || usage
      ROUTES_OVERRIDE_ARG="$2"
      shift 2
      ;;
    --agents-config)
      [ $# -ge 2 ] || usage
      AGENTS_CONFIG_ARG="$2"
      shift 2
      ;;

    --help|-h)
      usage
      ;;
    -*)
      echo "ERROR: unknown option: $1" >&2
      usage
      ;;
    *)
      if [ -z "$TICKET_ID" ]; then
        TICKET_ID="$1"
        shift
      elif [[ "$1" == *"="* ]]; then
        if [[ "$1" =~ ^agent\.[a-zA-Z0-9_-]+\.(subprocess|model)= ]]; then
          AGENT_ROUTE_FLAGS+=("$1")
        elif [[ "$1" =~ ^agents\.config= ]]; then
          AGENTS_CONFIG_ARG="${1#agents.config=}"
        else
          FLAG_OVERRIDES+=("$1")
        fi
        shift
      else
        echo "ERROR: unexpected argument: $1" >&2
        usage
      fi
      ;;
  esac
done

[ -n "$TICKET_ID" ] || usage

if [ -n "$REPO_ROOT_ARG" ]; then
  REPO_ROOT="$(cd "$REPO_ROOT_ARG" && pwd)"
else
  REPO_ROOT="${REPO_ROOT:-$(git -C "$_WORKTREE_ROOT" rev-parse --show-toplevel 2>/dev/null || true)}"
  if [ -z "$REPO_ROOT" ] || [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
    REPO_ROOT="$(pwd)"
    if [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
      echo "ERROR: spec/project.yaml not found (use --repo PATH or run from target repo)" >&2
      exit 7
    fi
  fi
fi

if [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
  echo "ERROR: spec/project.yaml not found under $REPO_ROOT" >&2
  exit 7
fi

if [ ! -x "$RUN_WORKFLOW" ]; then
  echo "ERROR: run-workflow.sh not found at $RUN_WORKFLOW" >&2
  exit 7
fi

export REPO_ROOT
export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
if [ -d "$_WORKTREE_ROOT/config" ]; then
  export ORCHESTRATOR_HOME="$_WORKTREE_ROOT"
fi

WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/.orchestrator}"
TICKET_SLUG="$(echo "$TICKET_ID" | tr '[:upper:]' '[:lower:]')"
_COMPLETE_DIR="$SCRIPT_DIR/complete"
if [ -d "$_WORKTREE_ROOT/orchestrator_next/scripts/complete" ]; then
  _COMPLETE_DIR="$_WORKTREE_ROOT/orchestrator_next/scripts/complete"
fi

resolve_archived_state_yaml() {
  local slug="$1"
  # Archived state lives in spec/changes/archive/ (gitted artifacts).
  if [ -f "$REPO_ROOT/spec/changes/archive/$slug/state.yaml" ]; then
    echo "$REPO_ROOT/spec/changes/archive/$slug/state.yaml"
    return 0
  fi
  local dated
  for dated in "$REPO_ROOT/spec/changes/archive"/*"-$slug"/state.yaml; do
    if [ -f "$dated" ]; then
      echo "$dated"
      return 0
    fi
  done
  return 1
}


# Active state lives in $REPO_ROOT/.orchestrator/<slug>/ as <ts>_<schema>_state.yaml.
resolve_state_yaml() {
  local slug="$1"
  local match
  match=$(ls "$WORKFLOW_STATE_DIR/$slug/"*_state.yaml 2>/dev/null | sort | tail -1 || true)
  if [ -f "$match" ]; then
    echo "$match"
    return 0
  fi
  return 1
}

STATE_YAML="$(resolve_state_yaml "$TICKET_SLUG" 2>/dev/null || true)"

# Rerun refusal is NOT decided here — it's the workflow's own decision, made by
# the optional `check-rerun` step (schemas that want it list it first). The
# engine/driver carries no archive knowledge.
#
# `complete` on an already-archived feature is different: the user explicitly
# invoked teardown, and the active state.yaml is gone because it was archived.
# Locate the archived state so merge-to-main / remove-worktree can finish. This
# is state resolution for an explicit op, not a rerun policy decision.
if [ "$SCHEMA" = "complete" ] && [ ! -f "${STATE_YAML:-}" ]; then
  STATE_YAML="$(resolve_archived_state_yaml "$TICKET_SLUG" 2>/dev/null || true)"
  if [ -f "${STATE_YAML:-}" ]; then
    echo "Resuming complete on archived state: $STATE_YAML" >&2
  fi
fi

if [ ! -f "${STATE_YAML:-}" ]; then
  if [ ! -f "$SEED_STATE" ]; then
    echo "ERROR: no state.yaml for $TICKET_SLUG and seed-state.sh missing at $SEED_STATE" >&2
    exit 7
  fi
  echo "Seeding workflow: slug=$TICKET_SLUG schema=$SCHEMA" >&2
  # shellcheck disable=SC2086
  bash "$SEED_STATE" "$TICKET_SLUG" "$SCHEMA" "${FLAG_OVERRIDES[@]}" || exit 7
  STATE_YAML="$(resolve_state_yaml "$TICKET_SLUG")" || STATE_YAML=""
fi

if [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: state.yaml not found at $STATE_YAML after seed" >&2
  exit 7
fi


if [ -n "$ROUTES_OVERRIDE_ARG" ]; then
  if [ ! -f "$ROUTES_OVERRIDE_ARG" ]; then
    echo "ERROR: --routes-override file not found: $ROUTES_OVERRIDE_ARG" >&2
    exit 7
  fi
  export ORCHESTRATOR_ROUTES_YAML
  ORCHESTRATOR_ROUTES_YAML=$(agent_routes_abs_path "$ROUTES_OVERRIDE_ARG")
  export ORCHESTRATOR_ROUTES_YAML
fi

if [ -n "$AGENTS_CONFIG_ARG" ]; then
  if ! ORCHESTRATOR_AGENTS_CONFIG=$(agent_routes_abs_path "$AGENTS_CONFIG_ARG"); then
    echo "ERROR: --agents-config file not found: $AGENTS_CONFIG_ARG" >&2
    exit 7
  fi
  export ORCHESTRATOR_AGENTS_CONFIG
fi

if [ "${#AGENT_ROUTE_FLAGS[@]}" -gt 0 ]; then
  export ORCHESTRATOR_AGENT_ROUTE_OVERRIDES
  ORCHESTRATOR_AGENT_ROUTE_OVERRIDES=$(agent_routes_build_overrides_from_flags "${AGENT_ROUTE_FLAGS[@]}")
  export ORCHESTRATOR_AGENT_ROUTE_OVERRIDES
fi

echo "Running shell workflow: ticket=$TICKET_ID schema=$SCHEMA state=$STATE_YAML" >&2
set +e
bash "$RUN_WORKFLOW" "$STATE_YAML" "$TICKET_ID"
_RC=$?
set -e

exit "$_RC"
