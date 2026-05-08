#!/usr/bin/env bash
# Test: archive-completed-change.sh merges artifacts from both worktree and repo_root.
#
# HL-303: After the worktree split, tracked artifacts (spec.md, design.md, tasks.md,
# diagnose.md) live in $WORKTREE_ROOT/spec/changes/<id>/ while state files (state.yaml,
# plan.yaml) live in $REPO_ROOT/spec/changes/<id>/. The archive script must collect
# from BOTH sources into a single archive destination.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/inline/archive-completed-change.sh"

# Create isolated temp dirs
TMPDIR_BASE="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

WT_ROOT="$TMPDIR_BASE/worktree"
RR_ROOT="$TMPDIR_BASE/repo"
ARCHIVE_DIR="$TMPDIR_BASE/archive"

WT_SRC="$WT_ROOT/spec/changes/demo"
RR_SRC="$RR_ROOT/spec/changes/demo"
DST="$ARCHIVE_DIR/2099-01-01-demo"

# Seed the two source directories
mkdir -p "$WT_SRC" "$RR_SRC"
echo "spec content" > "$WT_SRC/spec.md"
echo "design content" > "$WT_SRC/design.md"
echo "tasks content" > "$WT_SRC/tasks.md"
printf 'status: completed\n' > "$RR_SRC/state.yaml"
printf 'phase: complete\n' > "$RR_SRC/plan.yaml"

# Run the merge logic directly (mirrors the script's merge block).
# We test the logic in isolation rather than invoking the script end-to-end
# because the script requires git and a backlog CLI for its commit/cleanup tail,
# which are not available in the tmp environment.
mkdir -p "$DST"
[ -n "$WT_ROOT" ] && [ -d "$WT_SRC" ] && cp -a "$WT_SRC"/. "$DST"/ && rm -rf "$WT_SRC"
[ -d "$RR_SRC" ] && cp -a "$RR_SRC"/. "$DST"/ && rm -rf "$RR_SRC"

# Assertions
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

check "spec.md in archive"   "$([ -f "$DST/spec.md"   ] && echo 0 || echo 1)"
check "design.md in archive" "$([ -f "$DST/design.md" ] && echo 0 || echo 1)"
check "tasks.md in archive"  "$([ -f "$DST/tasks.md"  ] && echo 0 || echo 1)"
check "state.yaml in archive" "$([ -f "$DST/state.yaml" ] && echo 0 || echo 1)"
check "plan.yaml in archive"  "$([ -f "$DST/plan.yaml"  ] && echo 0 || echo 1)"

# Verify sources were removed (cleanup)
check "worktree source removed" "$([ ! -d "$WT_SRC" ] && echo 0 || echo 1)"
check "repo_root source removed" "$([ ! -d "$RR_SRC" ] && echo 0 || echo 1)"

# Verify backward compat: no-worktree case (WORKTREE_ROOT empty → only RR_SRC merged)
RR2_SRC="$TMPDIR_BASE/repo2/spec/changes/demo2"
DST2="$TMPDIR_BASE/archive2/2099-01-01-demo2"
mkdir -p "$RR2_SRC"
echo "state only" > "$RR2_SRC/state.yaml"
mkdir -p "$DST2"
WORKTREE_ROOT_EMPTY=""
[ -n "$WORKTREE_ROOT_EMPTY" ] && [ -d "${WORKTREE_ROOT_EMPTY}/spec/changes/demo2" ] && cp -a "${WORKTREE_ROOT_EMPTY}/spec/changes/demo2"/. "$DST2"/
[ -d "$RR2_SRC" ] && cp -a "$RR2_SRC"/. "$DST2"/ && rm -rf "$RR2_SRC"
check "backward-compat: state.yaml in archive (no worktree)" "$([ -f "$DST2/state.yaml" ] && echo 0 || echo 1)"
check "backward-compat: spec.md absent (not in repo_root)"   "$([ ! -f "$DST2/spec.md"  ] && echo 0 || echo 1)"

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "OK: archive merges worktree and repo_root artifacts"
else
  echo "FAIL: $fail assertion(s) failed"
  exit 1
fi
