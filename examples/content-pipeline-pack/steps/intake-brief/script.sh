#!/usr/bin/env bash
# intake-brief — resolve the <id> passed to `orchestrator content <id>` into a
# brief the downstream prompt steps read. Deterministic: no LLM, no network.
#
# Source of the brief, in order:
#   1. $REPO_ROOT/briefs/<change_id>.md  — an author-supplied brief
#   2. a placeholder stub, so the pipeline is runnable before any brief exists
#
# Writes brief.md into the artifact dir the engine chose for this run. That dir
# is $ORCHESTRATOR_WORKTREE_ARTIFACT_DIR, which the engine points at
# $REPO_ROOT/spec/changes when no worktree was created — this workflow never
# creates one, so nothing here assumes a branch or a worktree path.
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
change_id="${CHANGE_ID:-${ORCHESTRATOR_CHANGE_ID:?orchestrator: change id required}}"

ARTIFACT_BASE="${ORCHESTRATOR_WORKTREE_ARTIFACT_DIR:-${REPO_ROOT}/spec/changes}"
OUT_DIR="${ARTIFACT_BASE}/${change_id}"
mkdir -p "$OUT_DIR"

SRC="${REPO_ROOT}/briefs/${change_id}.md"
if [ -f "$SRC" ]; then
  cp "$SRC" "$OUT_DIR/brief.md"
  source_kind="authored"
else
  cat >"$OUT_DIR/brief.md" <<EOF
# Brief: ${change_id}

No authored brief found at briefs/${change_id}.md. This is a placeholder so the
pipeline stays runnable; treat the topic as "${change_id}" and state in the
outline that the brief was missing.

- Audience: unspecified
- Format: short article
- Length: unspecified
EOF
  source_kind="placeholder"
fi

printf '{"brief_path": "%s", "brief_source": "%s"}\n' "$OUT_DIR/brief.md" "$source_kind"
