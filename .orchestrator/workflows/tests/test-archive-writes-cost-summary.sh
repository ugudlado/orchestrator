#!/usr/bin/env bash
# Test: archive-completed-change moves cost-summary.md written by the cost-report step.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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
printf 'status: completed\n' > "$SRC/state.yaml"
echo "design" > "$SRC/design.md"
echo "# Cost Summary (from cost-report step)" > "$SRC/cost-summary.md"

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

check "archive dir created"        "$([ -d "$DST" ] && echo 0 || echo 1)"
check "state.yaml archived"        "$([ -f "$DST/state.yaml" ] && echo 0 || echo 1)"
check "cost-summary.md archived"   "$([ -f "$DST/cost-summary.md" ] && echo 0 || echo 1)"
check "cost-summary.md non-empty"  "$([ -s "$DST/cost-summary.md" ] && echo 0 || echo 1)"
check "script reported archived"   "$(echo "$OUT" | grep -q '"archived_at"' && echo 0 || echo 1)"

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "OK: archive moves cost-summary.md from cost-report step"
else
  echo "FAIL: $fail assertion(s) failed"
  exit 1
fi
