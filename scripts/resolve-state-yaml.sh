#!/usr/bin/env bash
# resolve-state-yaml.sh — unified state.yaml path resolver (worktree-aware).
#
# Lookup order (first existing file wins):
#   1. $WORKFLOW_STATE_DIR/<id>/state.yaml
#      (default: $REPO_ROOT/spec/changes/<id>/state.yaml)
#   2. $REPO_ROOT/spec/changes/archive/<id>/state.yaml
#   3. $WTBASE/spec/changes/archive/<id>/state.yaml
#   4. $REPO_ROOT/spec/changes/archive/*-<id>/state.yaml (legacy dated archives)
#
# Worktree base ($WTBASE) precedence:
#   $WORKTREE_ROOT → git worktree list (branch refs/heads/feature/<id>) →
#   $HOME/code/feature_worktrees/<id>
#
# Usage: resolve-state-yaml.sh <change-id> [repo-root]
#   change-id  — lowercased on input
#   repo-root  — defaults to $(git rev-parse --show-toplevel)

set -euo pipefail

CHANGE_ID="${1:?change-id required}"
REPO_ROOT="${2:-$(git rev-parse --show-toplevel)}"
CHANGE_ID="$(printf '%s' "$CHANGE_ID" | tr '[:upper:]' '[:lower:]')"

WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"

abs_path() {
  local p="$1" dir base
  dir="$(dirname "$p")"
  base="$(basename "$p")"
  echo "$(cd "$dir" && pwd)/$base"
}

discover_wtbase() {
  if [ -n "${WORKTREE_ROOT:-}" ]; then
    printf '%s' "$WORKTREE_ROOT"
    return 0
  fi

  local line wt_path="" branch_path
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      worktree\ *)
        wt_path="${line#worktree }"
        ;;
      branch\ *)
        branch_path="${line#branch }"
        if [[ "$branch_path" == */"$CHANGE_ID" ]] || [[ "$branch_path" == */feature/"$CHANGE_ID" ]]; then
          printf '%s' "$wt_path"
          return 0
        fi
        wt_path=""
        ;;
    esac
  done < <(git worktree list 2>/dev/null || true)

  printf '%s' "${HOME}/code/feature_worktrees/${CHANGE_ID}"
}

WTBASE="$(discover_wtbase)"

LEGACY_GLOB="$REPO_ROOT/spec/changes/archive/*-$CHANGE_ID/state.yaml"

# Ordered search paths (bash 3.2: indexed array, no mapfile/declare -A).
SEARCH_PATHS=()
SEARCH_PATHS+=("$WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml")
SEARCH_PATHS+=("$REPO_ROOT/spec/changes/archive/$CHANGE_ID/state.yaml")
SEARCH_PATHS+=("$WTBASE/spec/changes/archive/$CHANGE_ID/state.yaml")

for legacy in $LEGACY_GLOB; do
  if [ -f "$legacy" ]; then
    SEARCH_PATHS+=("$legacy")
  fi
done

MATCHES=()
for candidate in "${SEARCH_PATHS[@]}"; do
  if [ -f "$candidate" ]; then
    MATCHES+=("$(abs_path "$candidate")")
  fi
done

if [ "${#MATCHES[@]}" -eq 0 ]; then
  echo "ERROR: cannot locate state.yaml for $CHANGE_ID" >&2
  echo "Tried:" >&2
  echo "  ${SEARCH_PATHS[0]}" >&2
  echo "  ${SEARCH_PATHS[1]}" >&2
  echo "  ${SEARCH_PATHS[2]}" >&2
  echo "  $LEGACY_GLOB" >&2
  exit 1
fi

if [ "${#MATCHES[@]}" -gt 1 ]; then
  others="${MATCHES[1]}"
  i=2
  while [ "$i" -lt "${#MATCHES[@]}" ]; do
    others="$others, ${MATCHES[$i]}"
    i=$((i + 1))
  done
  echo "note: picked ${MATCHES[0]} over $others" >&2
fi

printf '%s\n' "${MATCHES[0]}"
