#!/usr/bin/env bash
# repro.sh — Demonstrates the writer/reader path mismatch for HL-303.
#
# The bug: workflow artifact writers (seed-state.sh, design-and-draft-artifacts)
# write to $REPO_ROOT/spec/changes/<id>/ while _resolve_tasks_md prefers
# $WORKTREE_PATH/spec/changes/<id>/ — a different directory when a worktree is in use.
# _check_all_tasks_completed fail-opens (returns True) when the file is missing,
# causing execute-next-task's repeat_until to immediately advance on the first run.
#
# Run: bash spec/changes/hl-303/repro.sh
# Expected: prints "Bug confirmed: fail-open returned True despite unchecked tasks"
# Actual (post-fix): prints "OK: predicate correctly detected unchecked tasks"

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
# Prefer the feature worktree's scripts when present (post-T-2 fix lives there).
WORKTREE_SCRIPTS="$REPO_ROOT/../feature_worktrees/hl-303/config/scripts"
if [ -d "$WORKTREE_SCRIPTS" ]; then
    SCRIPTS_DIR="$WORKTREE_SCRIPTS"
else
    SCRIPTS_DIR="$REPO_ROOT/config/scripts"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# -------------------------------------------------------------------
# Step 1: Writer writes tasks.md to $REPO_ROOT/spec/changes/<id>/
# (simulates design-and-draft-artifacts writing $WORKFLOW_STATE_DIR/$CHANGE_ID/tasks.md
# where WORKFLOW_STATE_DIR defaults to $REPO_ROOT/spec/changes per seed-state.sh:49)
# -------------------------------------------------------------------
WRITER_DIR="$TMP/repo/spec/changes/demo"
mkdir -p "$WRITER_DIR"
cat > "$WRITER_DIR/tasks.md" << 'MD'
## Tasks

- [ ] T-1: Fix the path mismatch bug
- [ ] T-2: Write regression test
MD

echo "[writer] tasks.md written to: $WRITER_DIR/tasks.md"
echo "[writer] content:"
cat "$WRITER_DIR/tasks.md"
echo ""

# -------------------------------------------------------------------
# Step 2: Reader (_resolve_tasks_md) is called with state that has
# worktree_path set to a DIFFERENT directory (the actual git worktree).
# This simulates the state.yaml produced by workflow-init, where
# worktree_path = $REPO_ROOT/../feature_worktrees/<slug> (not repo_root).
# The worktree does NOT have spec/changes/demo/tasks.md.
# -------------------------------------------------------------------
WORKTREE="$TMP/worktree"  # separate dir — no tasks.md here
mkdir -p "$WORKTREE"

STATE_DICT="$(cat <<PYDICT
{
    "change_id": "demo",
    "worktree_path": "$WORKTREE",
    "repo_root": "$TMP/repo"
}
PYDICT
)"

echo "[reader] state.yaml has:"
echo "  worktree_path = $WORKTREE  (NO tasks.md here)"
echo "  repo_root     = $TMP/repo"
echo ""

# -------------------------------------------------------------------
# Step 3: Invoke _check_all_tasks_completed via Python — show fail-open
# -------------------------------------------------------------------
RESULT=$(PYTHONPATH="$SCRIPTS_DIR" python3 - <<PYEOF
import sys, json
state = json.loads('''$STATE_DICT''')

from orchestrator_next.record import _resolve_tasks_md, _check_all_tasks_completed

resolved = _resolve_tasks_md(state)
print(f"[resolver] _resolve_tasks_md chose: {resolved}", file=sys.stderr)
print(f"[resolver] that path exists: {resolved.exists() if resolved else 'N/A'}", file=sys.stderr)

result = _check_all_tasks_completed(state)
print(str(result))
PYEOF
)

echo ""
if [ "$RESULT" = "True" ]; then
    echo "Bug confirmed: _check_all_tasks_completed returned True (fail-open)"
    echo "               despite unchecked tasks existing at $WRITER_DIR/tasks.md"
    echo "               The repeat_until: all_tasks_completed loop would IMMEDIATELY"
    echo "               advance execute-next-task — skipping all tasks."
    exit 0
else
    echo "OK: predicate correctly detected unchecked tasks (returned False)"
    echo "    This means the bug is already fixed."
    exit 1
fi
