#!/usr/bin/env bash
# Test: remove-worktree.sh uses safe branch delete (-d, not -D).
#
# Regression guard: scripts/inline/remove-worktree.sh previously used
# `git branch -D` which force-deletes branches regardless of merge status,
# silently destroying unmerged commits. `-d` (safe delete) is required.
# remove-worktree.sh is invoked as the remove-worktree step in complete.yaml.
#
# This test sets up a repo with an UNMERGED branch, invokes remove-worktree.sh,
# and asserts the branch survives with a warning emitted to stderr.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/orchestrator_next/scripts/complete/remove-worktree.sh"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

echo "=== Test: remove-worktree.sh safe branch delete (-d not -D) ==="

# ── Static check: no `branch -D` in the script ────────────────────────────
! grep -q 'branch -D' "$SCRIPT"
check "script does not use 'git branch -D' (force delete)" $?

grep -q 'branch -d' "$SCRIPT"
check "script uses 'git branch -d' (safe delete)" $?

# ── Behavioral test: unmerged branch survives removal attempt ─────────────
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/remove-worktree-test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT

MAIN_REPO="$TMP_DIR/main"
WORKTREE="$TMP_DIR/wt"

# Set up a repo with one commit on main
git init -q "$MAIN_REPO"
cd "$MAIN_REPO"
git config user.email test@test && git config user.name test
echo seed > seed.txt
git add seed.txt
git commit -q -m seed

# Create a worktree with an unmerged commit
git worktree add -q -b test-unmerged "$WORKTREE" >/dev/null 2>&1
cd "$WORKTREE"
echo new > new.txt
git add new.txt
git commit -q -m "unmerged commit"

cd "$MAIN_REPO"

# Invoke remove-worktree.sh
OUTPUT=$(REPO_ROOT="$MAIN_REPO" WORKTREE_PATH="$WORKTREE" BRANCH="test-unmerged" \
  bash "$SCRIPT" 2>&1)

# Worktree should be gone
[[ ! -d "$WORKTREE" ]]
check "worktree directory removed" $?

# Branch should still exist (unmerged, safe delete refused)
git -C "$MAIN_REPO" show-ref --verify --quiet refs/heads/test-unmerged
check "unmerged branch still exists (not destroyed)" $?

# Output should contain warning
echo "$OUTPUT" | grep -q "not fully merged"
check "stderr warning emitted for unmerged branch" $?

# JSON output should have branch_deleted: false
echo "$OUTPUT" | grep -q '"branch_deleted": false'
check "JSON reports branch_deleted: false" $?

# ── Behavioral test: merged branch IS deleted ─────────────────────────────
MAIN_REPO2="$TMP_DIR/main2"
WORKTREE2="$TMP_DIR/wt2"

git init -q "$MAIN_REPO2"
cd "$MAIN_REPO2"
git config user.email test@test && git config user.name test
echo seed > seed.txt
git add seed.txt
git commit -q -m seed

git worktree add -q -b test-merged "$WORKTREE2" >/dev/null 2>&1
cd "$WORKTREE2"
echo new > new.txt
git add new.txt
git commit -q -m "merge candidate"

# Merge it back to main
cd "$MAIN_REPO2"
git merge -q --no-ff test-merged -m "merge test-merged" >/dev/null 2>&1

OUTPUT2=$(REPO_ROOT="$MAIN_REPO2" WORKTREE_PATH="$WORKTREE2" BRANCH="test-merged" \
  bash "$SCRIPT" 2>&1)

# Branch should be gone (merged, safe delete succeeded)
! git -C "$MAIN_REPO2" show-ref --verify --quiet refs/heads/test-merged
check "merged branch deleted" $?

echo "$OUTPUT2" | grep -q '"branch_deleted": true'
check "JSON reports branch_deleted: true for merged branch" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
