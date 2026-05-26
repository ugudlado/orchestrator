#!/usr/bin/env bats
# End-to-end tests for qa-rework.sh against worktree-completed features.
# RED until T-6 wires qa-rework.sh through resolve-state-yaml.sh.

ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
QA_REWORK="$ORCHESTRATOR_ROOT/scripts/qa-rework.sh"
CHANGE_ID="orc-fixture"
BRANCH="feature/orc-fixture"
TICKET_ID="task-99"

setup() {
  TEST_REPO="$BATS_TMPDIR/test-repo-$$"
  WT_BASE="$BATS_TMPDIR/wt/$CHANGE_ID"
  FAKE_HOME="$BATS_TMPDIR/fake-home-$$"
  STUB_BIN="$BATS_TMPDIR/stubs-$$"

  mkdir -p "$STUB_BIN" "$FAKE_HOME"

  export TEST_REPO WT_BASE FAKE_HOME STUB_BIN
  export HOME="$FAKE_HOME"
  export PATH="$STUB_BIN:$PATH"
  unset WORKTREE_ROOT WORKFLOW_STATE_DIR

  write_backlog_stub
  setup_repo_and_worktree
}

teardown() {
  if [ -d "$TEST_REPO" ]; then
    git -C "$TEST_REPO" worktree remove "$WT_BASE" --force 2>/dev/null || true
  fi
  rm -rf "$TEST_REPO" "$WT_BASE" "$FAKE_HOME" "$STUB_BIN" "$BATS_TMPDIR/wt"
}

write_backlog_stub() {
  cat >"$STUB_BIN/backlog" <<'STUB'
#!/usr/bin/env bash
echo "$@" >> "${BACKLOG_STUB_LOG:-/dev/null}"
exit 0
STUB
  chmod +x "$STUB_BIN/backlog"
  export BACKLOG_STUB_LOG="$BATS_TMPDIR/backlog-stub-$$.log"
  : >"$BACKLOG_STUB_LOG"
}

setup_repo_and_worktree() {
  mkdir -p "$TEST_REPO/spec"
  git -C "$TEST_REPO" init -q
  git -C "$TEST_REPO" config user.email "test@example.com"
  git -C "$TEST_REPO" config user.name "Test"
  printf 'version: 1\nticketing: backlog\n' >"$TEST_REPO/spec/project.yaml"
  echo "readme" >"$TEST_REPO/README.md"
  git -C "$TEST_REPO" add .
  git -C "$TEST_REPO" commit -q -m "init"
  git -C "$TEST_REPO" branch -m main
  git -C "$TEST_REPO" checkout -q -b "$BRANCH"
  echo "feature" >>"$TEST_REPO/README.md"
  git -C "$TEST_REPO" add README.md
  git -C "$TEST_REPO" commit -q -m "feature work"
  git -C "$TEST_REPO" checkout -q main
  mkdir -p "$(dirname "$WT_BASE")"
  git -C "$TEST_REPO" worktree add -q "$WT_BASE" "$BRANCH"
}

write_worktree_archived_state() {
  local archive_dir="$WT_BASE/spec/changes/archive/$CHANGE_ID"
  mkdir -p "$archive_dir"
  cat >"$archive_dir/state.yaml" <<YAML
change_id: $CHANGE_ID
schema: feature
status: completed
repo_root: $TEST_REPO
worktree_path: $WT_BASE
branch: $BRANCH
ticket_id: $TICKET_ID
flags:
  worktree: true
YAML
}

run_qa_rework() {
  local id="${1:-$CHANGE_ID}"
  run bash "$QA_REWORK" "$id" "$TEST_REPO"
}

@test "worktree-completed feature: qa-rework resolves archive state and syncs In Progress" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"

  run_qa_rework
  [ "$status" -eq 0 ]

  grep -qF "task edit $TICKET_ID" "$BACKLOG_STUB_LOG"
  grep -qF "In Progress" "$BACKLOG_STUB_LOG"
}

@test "after qa-rework: feature branch is still listed" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"

  run_qa_rework
  [ "$status" -eq 0 ]

  git -C "$TEST_REPO" branch -a | grep -qF "$BRANCH"
}

@test "after qa-rework: worktree path remains in git worktree list" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"

  run_qa_rework
  [ "$status" -eq 0 ]

  git -C "$TEST_REPO" worktree list | grep -qF "$WT_BASE"
}

@test "missing state: qa-rework exits 1 before ticket-sync (backlog) is invoked" {
  : >"$BACKLOG_STUB_LOG"

  run_qa_rework "missing-change-id"
  [ "$status" -eq 1 ]
  [ ! -s "$BACKLOG_STUB_LOG" ]
}
