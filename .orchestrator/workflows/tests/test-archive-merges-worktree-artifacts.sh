#!/usr/bin/env bash
# Test: archive-completed-change.sh moves all files from the worktree source.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/steps/archive-completed-change/script.sh"

TMPDIR_BASE="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

WT_ROOT="$TMPDIR_BASE/worktree"
FAKE_REPO="$TMPDIR_BASE/repo"
CHANGE_ID="demo"
ARCHIVE_REL="spec/changes/archive/2099-01-01-demo"
SRC="$WT_ROOT/spec/changes/$CHANGE_ID"
DST="$WT_ROOT/$ARCHIVE_REL"

mkdir -p "$SRC"
echo "design content"  > "$SRC/design.md"
printf 'version: 1\ntasks: []\n' > "$SRC/tasks.yaml"
echo "diagnose content" > "$SRC/diagnose.md"
printf 'status: completed\n' > "$SRC/state.yaml"
printf 'phase: complete\n'   > "$SRC/plan.yaml"

mkdir -p "$FAKE_REPO/scripts"
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" config user.email test@test
git -C "$FAKE_REPO" config user.name test
git -C "$WT_ROOT" init -q
git -C "$WT_ROOT" config user.email test@test
git -C "$WT_ROOT" config user.name test

OUT=$(REPO_ROOT="$FAKE_REPO" CHANGE_ID="$CHANGE_ID" ARCHIVE_PATH="$ARCHIVE_REL" \
  WORKTREE_ROOT="$WT_ROOT" ORCHESTRATOR_WORKFLOW_DIR="$WT_ROOT" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  bash "$SCRIPT" 2>/dev/null)

fail=0
check() {
  local desc="$1" result="$2"
  if [[ "$result" -eq 0 ]]; then echo "PASS: $desc"
  else echo "FAIL: $desc"; ((fail++))
  fi
}

check "design.md in archive"   "$([ -f "$DST/design.md"   ] && echo 0 || echo 1)"
check "tasks.yaml in archive"  "$([ -f "$DST/tasks.yaml"  ] && echo 0 || echo 1)"
check "diagnose.md in archive" "$([ -f "$DST/diagnose.md" ] && echo 0 || echo 1)"
check "state.yaml in archive"  "$([ -f "$DST/state.yaml"  ] && echo 0 || echo 1)"
check "plan.yaml in archive"   "$([ -f "$DST/plan.yaml"   ] && echo 0 || echo 1)"
check "active session dir moved" "$([ ! -d "$SRC" ] && echo 0 || echo 1)"
check "archive under worktree" "$([ -d "$DST" ] && echo 0 || echo 1)"
check "script reported archived" "$(echo "$OUT" | grep -q '"archived_at"' && echo 0 || echo 1)"

NO_WT_RESULT=$(REPO_ROOT="$FAKE_REPO" CHANGE_ID="$CHANGE_ID" ARCHIVE_PATH="$ARCHIVE_REL" \
  WORKTREE_ROOT="" ORCHESTRATOR_WORKFLOW_DIR="" \
  bash "$SCRIPT" 2>/dev/null)
check "fails when WORKTREE_ROOT unset and no active dir" \
  "$(echo "$NO_WT_RESULT" | grep -q '"skipped": true' && echo 0 || echo 1)"

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "OK: archive moves all files from worktree source"
else
  echo "FAIL: $fail assertion(s) failed"
  exit 1
fi
