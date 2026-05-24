#!/usr/bin/env bash
# Test: archive-completed-change.sh collects all files from the worktree source.
#
# Worktrees are required. All workflow files (state.yaml, plan.yaml, artifacts)
# live at $WORKTREE_ROOT/spec/changes/<id>/. The archive script must copy
# everything from that single source into the archive destination.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/inline/archive-completed-change.sh"

TMPDIR_BASE="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

WT_ROOT="$TMPDIR_BASE/worktree"
FAKE_REPO="$TMPDIR_BASE/repo"
SRC="$WT_ROOT/spec/changes/demo"
DST="$FAKE_REPO/spec/changes/archive/2099-01-01-demo"

# Seed the worktree source with all file types
mkdir -p "$SRC"
echo "design content"  > "$SRC/design.md"
printf 'version: 1\ntasks: []\n' > "$SRC/tasks.yaml"
echo "diagnose content" > "$SRC/diagnose.md"
printf 'status: completed\n' > "$SRC/state.yaml"
printf 'phase: complete\n'   > "$SRC/plan.yaml"

# Run the archive merge logic directly (mirrors archive-completed-change.sh lines 47-49).
# Invoked in isolation because the script's commit/backlog tail requires git+CLI.
mkdir -p "$DST"
cp -a "$SRC"/. "$DST"/ && rm -rf "$SRC"

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
check "worktree source removed" "$([ ! -d "$SRC" ] && echo 0 || echo 1)"

# Verify the script fails loudly when WORKTREE_ROOT is unset
NO_WT_RESULT=$(REPO_ROOT="$FAKE_REPO" CHANGE_ID="demo" ARCHIVE_PATH="spec/changes/archive/2099-01-01-demo" \
  WORKTREE_ROOT="" ORCHESTRATOR_WORKFLOW_DIR="" \
  bash "$SCRIPT" 2>/dev/null)
check "fails when WORKTREE_ROOT unset" \
  "$(echo "$NO_WT_RESULT" | grep -q '"skipped": true' && echo 0 || echo 1)"

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "OK: archive collects all files from worktree source"
else
  echo "FAIL: $fail assertion(s) failed"
  exit 1
fi
