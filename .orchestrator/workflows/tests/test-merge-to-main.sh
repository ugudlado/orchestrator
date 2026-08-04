#!/usr/bin/env bash
# test-merge-to-main.sh — merge-to-main.sh merges feature branch into default branch.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO_ROOT/orchestrator_next/scripts/complete/merge-to-main.sh"
PASS=0
FAIL=0

check() {
  local msg="$1"
  shift
  if "$@"; then
    echo "PASS: $msg"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $msg"
    FAIL=$((FAIL + 1))
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

MAIN_REPO="$TMP/main"
mkdir -p "$MAIN_REPO"
git -C "$MAIN_REPO" init -q
git -C "$MAIN_REPO" config user.email "test@example.com"
git -C "$MAIN_REPO" config user.name "Test"
echo main > "$MAIN_REPO/README.md"
git -C "$MAIN_REPO" add README.md
git -C "$MAIN_REPO" commit -q -m "init"

BRANCH="feature/demo-slug"
git -C "$MAIN_REPO" checkout -q -b "$BRANCH"
echo feature >> "$MAIN_REPO/README.md"
git -C "$MAIN_REPO" add README.md
git -C "$MAIN_REPO" commit -q -m "feature work"
git -C "$MAIN_REPO" checkout -q main

STATE_DIR="$MAIN_REPO/spec/changes/demo-slug"
mkdir -p "$STATE_DIR"
cat > "$STATE_DIR/state.yaml" <<EOF
change_id: demo-slug
branch: $BRANCH
repo_root: $MAIN_REPO
archive_path: spec/changes/archive/2099-01-01-demo-slug/
EOF

OUTPUT="$(REPO_ROOT="$MAIN_REPO" BRANCH="$BRANCH" CHANGE_ID=demo-slug bash "$SCRIPT")"
check "merge script exits 0" test $? -eq 0
echo "$OUTPUT" | grep -q '"merged": true'
check "reports merged" test $? -eq 0
git -C "$MAIN_REPO" branch --merged main | grep -q "$BRANCH"
check "feature branch merged into main" test $? -eq 0

echo ""
echo "Results: $PASS passed, $FAIL failed"
test "$FAIL" -eq 0
