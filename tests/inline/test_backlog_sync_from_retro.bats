#!/usr/bin/env bats

# Contract tests for config/scripts/inline/backlog-sync-from-retro.sh
# RED phase (T-1): fails until the helper exists (T-2).

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/inline/backlog-sync-from-retro.sh"
FIXTURES="$BATS_TEST_DIRNAME/fixtures"
FEATURE_ID="orc-91-test"
TEST_REPO="$BATS_TMPDIR/backlog-sync-repo"
STUB_DIR="$BATS_TMPDIR/backlog-stubs"

setup() {
  mkdir -p "$STUB_DIR" "$TEST_REPO/spec"
  export BACKLOG_LOG="$BATS_TMPDIR/backlog_calls.log"
  export BACKLOG_CREATE_COUNTER_FILE="$BATS_TMPDIR/backlog_create_counter"
  echo 0 > "$BACKLOG_CREATE_COUNTER_FILE"
  unset BACKLOG_SEARCH_STDOUT BACKLOG_SEARCH_EXIT
  unset BACKLOG_CREATE_STDOUT BACKLOG_CREATE_EXIT
  unset BACKLOG_EDIT_EXIT BACKLOG_FAIL_ON_CREATE_N
  write_backlog_stub
  export PATH="$STUB_DIR:/usr/bin:/bin"
}

teardown() {
  rm -rf "$STUB_DIR" "$TEST_REPO"
}

write_backlog_stub() {
  cat > "$STUB_DIR/backlog" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "${BACKLOG_LOG:?}"

subcmd="${1:-}"
shift || true

case "$subcmd" in
  search)
    if [[ -n "${BACKLOG_SEARCH_STDOUT:-}" ]]; then
      printf '%s\n' "$BACKLOG_SEARCH_STDOUT"
    fi
    exit "${BACKLOG_SEARCH_EXIT:-0}"
    ;;
  task)
    task_sub="${1:-}"
    shift || true
    case "$task_sub" in
      create)
        n=$(cat "${BACKLOG_CREATE_COUNTER_FILE:?}")
        n=$(( n + 1 ))
        echo "$n" > "${BACKLOG_CREATE_COUNTER_FILE:?}"
        if [[ -n "${BACKLOG_FAIL_ON_CREATE_N:-}" && "$BACKLOG_FAIL_ON_CREATE_N" == "$n" ]]; then
          echo "simulated backlog create failure" >&2
          exit 1
        fi
        if [[ -n "${BACKLOG_CREATE_STDOUT:-}" ]]; then
          echo "$BACKLOG_CREATE_STDOUT"
        else
          echo "Created task task-new-${n}"
        fi
        exit "${BACKLOG_CREATE_EXIT:-0}"
        ;;
      edit)
        if [[ "${BACKLOG_FAIL_EDIT:-}" == 1 ]]; then
          echo "simulated backlog edit failure" >&2
          exit 1
        fi
        exit "${BACKLOG_EDIT_EXIT:-0}"
        ;;
    esac
    ;;
esac
exit 0
STUB
  chmod +x "$STUB_DIR/backlog"
  : > "$BACKLOG_LOG"
}

install_project_yaml() {
  local fixture="$1"
  cp "$FIXTURES/$fixture" "$TEST_REPO/spec/project.yaml"
}

run_sync() {
  local retro_path="$1"
  local feature_id="${2:-$FEATURE_ID}"
  cd "$TEST_REPO" || return 1
  run bash "$SCRIPT" "$retro_path" "$feature_id"
}

count_log_matches() {
  local pattern="$1"
  grep -c -E "$pattern" "$BACKLOG_LOG" 2>/dev/null || true
}

@test "UC-1: new issue creates one ticket with recurrence-1, from-retro labels and fix_direction AC" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=""

  run_sync "$FIXTURES/retro_new_issue.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] sync: ISSUE-1 → created"* ]]
  [[ "$output" == *"[learn] Backlog sync: 1 created, 0 bumped, 0 regressions"* ]]

  [ "$(count_log_matches 'search')" -eq 1 ]
  [ "$(count_log_matches 'task create')" -eq 1 ]
  [ "$(count_log_matches 'recurrence-1')" -ge 1 ]
  [ "$(count_log_matches 'from-retro')" -ge 1 ]
  grep -q -- '--ac' "$BACKLOG_LOG"
  grep -q 'Add backlog-sync-from-retro.sh and invoke it from workflow-learner section 4b on every learn run' "$BACKLOG_LOG"
}

@test "UC-2: dedup_key match on open ticket appends recurrence note without creating a ticket" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=$'task-5\tOpen backlog sync ticket\tIn Progress'

  run_sync "$FIXTURES/retro_recurrence_open.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] sync: ISSUE-2 → bumped task-5"* ]]
  [[ "$output" == *"[learn] Backlog sync: 0 created, 1 bumped, 0 regressions"* ]]

  [ "$(count_log_matches 'task create')" -eq 0 ]
  [ "$(count_log_matches 'task edit task-5')" -eq 1 ]
  grep -q "Recurred in feature $FEATURE_ID" "$BACKLOG_LOG"
  grep -q 'detail: Same gap resurfaced' "$BACKLOG_LOG"
}

@test "UC-3: dedup_key match on Done ticket appends note and files Regression ticket" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=$'task-9\tClosed backlog sync ticket\tDone'

  run_sync "$FIXTURES/retro_recurrence_done.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] sync: ISSUE-3 → regression"* ]]
  [[ "$output" == *"[learn] Backlog sync: 0 created, 0 bumped, 1 regressions"* ]]

  [ "$(count_log_matches 'task edit task-9')" -eq 1 ]
  grep -q "Recurred in feature $FEATURE_ID" "$BACKLOG_LOG"
  grep -q 'Regression: Closed backlog sync ticket (task-9) recurred after close' "$BACKLOG_LOG"
  grep -q -- '--priority high' "$BACKLOG_LOG"
  grep -q 'regression' "$BACKLOG_LOG"
  [ "$(count_log_matches 'task create')" -eq 1 ]
}

@test "AC-4: per-issue audit lines and summary counts appear on stdout" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=""

  run_sync "$FIXTURES/retro_new_issue.md"

  [ "$status" -eq 0 ]
  [[ "$output" =~ \[learn\]\ sync:\ ISSUE-[0-9]+\ →\  ]]
  [[ "$output" =~ \[learn\]\ Backlog\ sync:\ [0-9]+\ created,\ [0-9]+\ bumped,\ [0-9]+\ regressions ]]
}

@test "UC-5: explicit backlog_entry slug is used verbatim as backlog search query" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=""

  run_sync "$FIXTURES/retro_explicit_backlog_entry.md"

  [ "$status" -eq 0 ]
  grep -q 'search orchestrator-doctor-stale-state-detector --plain' "$BACKLOG_LOG"
  ! grep -q 'workflow-gap|' "$BACKLOG_LOG"
}

@test "UC-E6: ticketing=linear skips sync without invoking backlog CLI" {
  install_project_yaml project_yaml_linear.yaml

  run_sync "$FIXTURES/retro_new_issue.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] Backlog sync: skipped — ticketing=linear"* ]]
  [ ! -s "$BACKLOG_LOG" ]
}

@test "UC-E1: missing retro.md exits 0 with no retro issues found" {
  install_project_yaml project_yaml_backlog.yaml

  run_sync "$BATS_TMPDIR/does-not-exist-retro.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] Backlog sync: no retro issues found"* ]]
  [ ! -s "$BACKLOG_LOG" ]
}

@test "UC-E3: prose-only retro exits 0 with no retro issues found" {
  install_project_yaml project_yaml_backlog.yaml

  run_sync "$FIXTURES/retro_prose_only.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] Backlog sync: no retro issues found"* ]]
  [ ! -s "$BACKLOG_LOG" ]
}

@test "UC-E4: two issues with same dedup_key create one ticket then append recurrence" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=""

  run_sync "$FIXTURES/retro_self_dedup.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] sync: ISSUE-4 → created"* ]]
  [[ "$output" == *"[learn] sync: ISSUE-5 → bumped"* ]]
  [[ "$output" == *"[learn] Backlog sync: 1 created, 1 bumped, 0 regressions"* ]]

  [ "$(count_log_matches 'task create')" -eq 1 ]
  [ "$(count_log_matches 'task edit')" -eq 1 ]
  grep -q 'Recurred in feature' "$BACKLOG_LOG"
}

@test "UC-E5: backlog CLI failure logs ERROR for issue and continues; exit 0 overall" {
  install_project_yaml project_yaml_backlog.yaml
  export BACKLOG_SEARCH_STDOUT=""
  export BACKLOG_FAIL_ON_CREATE_N=1

  run_sync "$FIXTURES/retro_cli_failure.md"

  [ "$status" -eq 0 ]
  [[ "$output" == *"[learn] sync: ISSUE-7 → ERROR (simulated backlog create failure)"* ]]
  [[ "$output" == *"[learn] sync: ISSUE-8 → created"* ]]
  [ "$(count_log_matches 'task create')" -eq 1 ]
}
