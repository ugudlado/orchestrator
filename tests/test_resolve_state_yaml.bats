#!/usr/bin/env bats
# Unit tests for orchestrator_next/scripts/metrics/resolve-state-yaml.sh

ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
RESOLVER="$ORCHESTRATOR_ROOT/orchestrator_next/scripts/metrics/resolve-state-yaml.sh"
CHANGE_ID="orc-fixture"

setup() {
  TEST_REPO="$BATS_TMPDIR/test-repo-$$"
  FAKE_HOME="$BATS_TMPDIR/fake-home-$$"
  STUB_DIR="$BATS_TMPDIR/stubs-$$"
  mkdir -p "$TEST_REPO/spec/changes" "$FAKE_HOME" "$STUB_DIR"
  git -C "$TEST_REPO" init -q
  git -C "$TEST_REPO" config user.email "test@example.com"
  git -C "$TEST_REPO" config user.name "Test"

  export TEST_REPO
  export FAKE_HOME
  export HOME="$FAKE_HOME"
  export PATH="$STUB_DIR:$PATH"
  unset WORKTREE_ROOT WORKFLOW_STATE_DIR
  unset GIT_WORKTREE_LIST_OUTPUT GIT_REV_PARSE_TOPLEVEL

  write_git_stub
}

teardown() {
  rm -rf "$TEST_REPO" "$FAKE_HOME" "$STUB_DIR"
}

write_git_stub() {
  cat > "$STUB_DIR/git" <<'GIT'
#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  printf '%s' "${GIT_WORKTREE_LIST_OUTPUT:-}"
  exit 0
fi
if [[ "$1" == "rev-parse" && "$2" == "--show-toplevel" ]]; then
  echo "${GIT_REV_PARSE_TOPLEVEL:-${TEST_REPO:-.}}"
  exit 0
fi
exec /usr/bin/git "$@"
GIT
  chmod +x "$STUB_DIR/git"
}

write_minimal_state() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  printf 'change_id: %s\n' "$CHANGE_ID" >"$path"
}

abs_path() {
  local p="$1" dir base
  dir="$(dirname "$p")"
  base="$(basename "$p")"
  echo "$(cd "$dir" && pwd)/$base"
}

run_resolve() {
  local change_id="${1:-$CHANGE_ID}"
  STDERR_FILE="$BATS_TMPDIR/resolve-stderr-$$"
  # Redirect must live inside bash -c: bats run merges stderr into $output otherwise.
  run bash -c "\"$RESOLVER\" \"${change_id}\" \"${TEST_REPO}\" 2>\"${STDERR_FILE}\""
  RESOLVE_STDERR="$(cat "$STDERR_FILE")"
}

@test "live state at WORKFLOW_STATE_DIR/<id>/state.yaml resolves to that path" {
  local live="$TEST_REPO/spec/changes/$CHANGE_ID/state.yaml"
  write_minimal_state "$live"
  export WORKFLOW_STATE_DIR="$TEST_REPO/spec/changes"

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$live")" ]
}

@test "main archive resolves when no live state exists" {
  local archived="$TEST_REPO/spec/changes/archive/$CHANGE_ID/state.yaml"
  write_minimal_state "$archived"

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$archived")" ]
}

@test "worktree archive resolves when no live or main archive; WORKTREE_ROOT honored" {
  local wt_root="$BATS_TMPDIR/wt-root-$$"
  local archived="$wt_root/spec/changes/archive/$CHANGE_ID/state.yaml"
  write_minimal_state "$archived"
  export WORKTREE_ROOT="$wt_root"

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$archived")" ]
}

@test "legacy dated archive resolves as last fallback" {
  local legacy="$TEST_REPO/spec/changes/archive/2026-05-01-$CHANGE_ID/state.yaml"
  write_minimal_state "$legacy"

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$legacy")" ]
}

@test "no candidate exists: exit 1 and stderr lists four tried paths" {
  run_resolve

  [ "$status" -eq 1 ]
  [[ "$RESOLVE_STDERR" == *"$TEST_REPO/spec/changes/$CHANGE_ID/state.yaml"* ]]
  [[ "$RESOLVE_STDERR" == *"$TEST_REPO/spec/changes/archive/$CHANGE_ID/state.yaml"* ]]
  [[ "$RESOLVE_STDERR" == *"$FAKE_HOME/code/feature_worktrees/$CHANGE_ID/spec/changes/archive/$CHANGE_ID/state.yaml"* ]]
  [[ "$RESOLVE_STDERR" == *"$TEST_REPO/spec/changes/archive/"*"$CHANGE_ID"* ]]
}

@test "two candidates: live wins and stderr notes pick over worktree archive" {
  local live="$TEST_REPO/spec/changes/$CHANGE_ID/state.yaml"
  local wt_root="$BATS_TMPDIR/wt-dual-$$"
  local wt_archived="$wt_root/spec/changes/archive/$CHANGE_ID/state.yaml"
  write_minimal_state "$live"
  write_minimal_state "$wt_archived"
  export WORKTREE_ROOT="$wt_root"

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$live")" ]
  [[ "$RESOLVE_STDERR" == *"note: picked"* ]]
  [[ "$RESOLVE_STDERR" == *"over"* ]]
  [[ "$RESOLVE_STDERR" == *"$(abs_path "$wt_archived")"* ]]
}

@test "WORKTREE_ROOT unset and git worktree list empty falls back to HOME/code/feature_worktrees/<id>" {
  local wt_base="$FAKE_HOME/code/feature_worktrees/$CHANGE_ID"
  local archived="$wt_base/spec/changes/archive/$CHANGE_ID/state.yaml"
  write_minimal_state "$archived"
  export GIT_WORKTREE_LIST_OUTPUT=""

  unset WORKTREE_ROOT
  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$archived")" ]
}

@test "git worktree list match used when WORKTREE_ROOT unset" {
  local listed_wt="$BATS_TMPDIR/listed-wt-$$"
  local archived="$listed_wt/spec/changes/archive/$CHANGE_ID/state.yaml"
  write_minimal_state "$archived"
  export GIT_WORKTREE_LIST_OUTPUT=$'worktree '"$listed_wt"$'\nHEAD abc\nbranch refs/heads/feature/'"$CHANGE_ID"$'\n'

  unset WORKTREE_ROOT
  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$archived")" ]
}

@test "worktree-live state resolves via WTBASE when no main-repo state exists (real-dispatch shape)" {
  # Mirrors production: REPO_ROOT is the main checkout (no live state there),
  # WORKTREE_ROOT points at the worktree, and the in-flight run's state lives at
  # $WTBASE/spec/changes/<id>/state.yaml (live, not archived). WORKFLOW_STATE_DIR
  # is intentionally NOT set — real inline dispatch never exports it.
  local wt_root="$BATS_TMPDIR/wt-live-$$"
  local wt_live="$wt_root/spec/changes/$CHANGE_ID/state.yaml"
  write_minimal_state "$wt_live"
  export WORKTREE_ROOT="$wt_root"
  unset WORKFLOW_STATE_DIR

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$wt_live")" ]
}

@test "worktree-live beats worktree-archive (live precedence)" {
  local wt_root="$BATS_TMPDIR/wt-live-arch-$$"
  local wt_live="$wt_root/spec/changes/$CHANGE_ID/state.yaml"
  local wt_arch="$wt_root/spec/changes/archive/$CHANGE_ID/state.yaml"
  write_minimal_state "$wt_live"
  write_minimal_state "$wt_arch"
  export WORKTREE_ROOT="$wt_root"
  unset WORKFLOW_STATE_DIR

  run_resolve
  [ "$status" -eq 0 ]
  [ "$output" = "$(abs_path "$wt_live")" ]
}
