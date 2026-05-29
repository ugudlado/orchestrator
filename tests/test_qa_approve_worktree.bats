#!/usr/bin/env bats
# End-to-end tests for qa-approve.sh against worktree-completed features.
# RED until T-4 wires qa-approve.sh through resolve-state-yaml.sh + remove-worktree.sh.

ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
QA_APPROVE="$ORCHESTRATOR_ROOT/scripts/qa-approve.sh"
CHANGE_ID="orc-fixture"
BRANCH="feature/orc-fixture"
TICKET_ID="task-99"

setup() {
  TEST_REPO="$BATS_TMPDIR/test-repo-$$"
  WT_BASE="$BATS_TMPDIR/wt/$CHANGE_ID"
  FAKE_HOME="$BATS_TMPDIR/fake-home-$$"
  FAKE_ORCH="$BATS_TMPDIR/fake-orch-$$"
  STUB_BIN="$BATS_TMPDIR/stubs-$$"
  MERGE_STUB_LOG="$BATS_TMPDIR/merge-stub-$$.log"
  REMOVE_STUB_LOG="$BATS_TMPDIR/remove-stub-$$.log"

  mkdir -p "$STUB_BIN" "$FAKE_HOME" "$FAKE_ORCH/scripts/inline"
  : >"$MERGE_STUB_LOG"
  : >"$REMOVE_STUB_LOG"

  export TEST_REPO WT_BASE FAKE_HOME FAKE_ORCH STUB_BIN
  export MERGE_STUB_LOG REMOVE_STUB_LOG
  export HOME="$FAKE_HOME"
  export PATH="$STUB_BIN:$PATH"
  export ORCHESTRATOR_HOME="$FAKE_ORCH"
  unset WORKTREE_ROOT WORKFLOW_STATE_DIR

  write_backlog_stub
  write_inline_stubs
  setup_repo_and_worktree
}

teardown() {
  if [ -d "$TEST_REPO" ]; then
    git -C "$TEST_REPO" worktree remove "$WT_BASE" --force 2>/dev/null || true
  fi
  rm -rf "$TEST_REPO" "$WT_BASE" "$FAKE_HOME" "$FAKE_ORCH" "$STUB_BIN" \
    "$MERGE_STUB_LOG" "$REMOVE_STUB_LOG" "$BATS_TMPDIR/wt"
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

write_inline_stubs() {
  cp "$ORCHESTRATOR_ROOT/scripts/inline/_read_state_env.sh" \
    "$FAKE_ORCH/scripts/inline/_read_state_env.sh"

  cat >"$FAKE_ORCH/scripts/inline/merge-to-main.sh" <<'STUB'
#!/usr/bin/env bash
printf 'STATE_YAML_PATH=%s\n' "${STATE_YAML_PATH:-}" >> "${MERGE_STUB_LOG:?}"
printf '%s\n' '{"merge_record": {"merged": true, "skipped": false, "branch": "feature/orc-fixture", "default_branch": "main", "merge_sha": "deadbeef"}}'
STUB
  chmod +x "$FAKE_ORCH/scripts/inline/merge-to-main.sh"

  cat >"$FAKE_ORCH/scripts/inline/remove-worktree.sh" <<'STUB'
#!/usr/bin/env bash
printf 'remove-worktree called STATE_YAML_PATH=%s\n' "${STATE_YAML_PATH:-}" >> "${REMOVE_STUB_LOG:?}"
# shellcheck source=./_read_state_env.sh
source "$(dirname "$0")/_read_state_env.sh"
read_state_env "$STATE_YAML_PATH" WORKTREE_PATH REPO_ROOT BRANCH
WORKTREE_PATH="${WORKTREE_PATH/#\~/$HOME}"
if [ -n "$WORKTREE_PATH" ] && [ -d "$WORKTREE_PATH" ]; then
  git -C "$REPO_ROOT" worktree remove "$WORKTREE_PATH" --force 2>/dev/null || true
fi
printf '%s\n' '{"removed": true, "worktree_path": "'"$WORKTREE_PATH"'", "branch": "'"$BRANCH"'"}'
STUB
  chmod +x "$FAKE_ORCH/scripts/inline/remove-worktree.sh"
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

abs_path() {
  local p="$1" dir base
  dir="$(dirname "$p")"
  base="$(basename "$p")"
  echo "$(cd "$dir" && pwd)/$base"
}

run_qa_approve() {
  local id="${1:-$CHANGE_ID}"
  run bash "$QA_APPROVE" "$id" "$TEST_REPO"
}

@test "worktree-completed feature: qa-approve by change-id exits 0" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"

  run_qa_approve
  [ "$status" -eq 0 ]
}

@test "after qa-approve: feature branch is not listed" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"

  run_qa_approve
  [ "$status" -eq 0 ]

  ! git -C "$TEST_REPO" branch -a | grep -qF "$BRANCH"
}

@test "after qa-approve: worktree path is not in git worktree list" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"

  run_qa_approve
  [ "$status" -eq 0 ]

  ! git -C "$TEST_REPO" worktree list | grep -qF "$WT_BASE"
}

@test "merge-to-main stub receives worktree-archive STATE_YAML_PATH" {
  write_worktree_archived_state
  export WORKTREE_ROOT="$WT_BASE"
  local expected
  expected="$(abs_path "$WT_BASE/spec/changes/archive/$CHANGE_ID/state.yaml")"

  run_qa_approve
  [ "$status" -eq 0 ]

  grep -qF "STATE_YAML_PATH=$expected" "$MERGE_STUB_LOG"
}

@test "missing state: qa-approve exits 1 before merge stub is invoked" {
  : >"$MERGE_STUB_LOG"

  run_qa_approve "missing-change-id"
  [ "$status" -eq 1 ]
  [ ! -s "$MERGE_STUB_LOG" ]
}
