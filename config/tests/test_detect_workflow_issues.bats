#!/usr/bin/env bats
# detect-workflow-issues.sh — shared detection helper for workflow-mechanics
# issues. Contract: config/steps/contracts/workflow-issues.md.

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
HELPER="$REPO_ROOT/scripts/lib/detect-workflow-issues.sh"

setup() {
  SANDBOX="$BATS_TMPDIR/detect-wfi-$$-$RANDOM"
  mkdir -p "$SANDBOX"
}

teardown() {
  rm -rf "$SANDBOX"
}

_jq_len() { jq -e 'length' <<<"$1"; }
_jq_first() { jq -e -r ".[0].$2" <<<"$1"; }

@test "no args -> empty array" {
  run bash "$HELPER"
  [ "$status" -eq 0 ]
  [ "$(jq -e 'length' <<<"$output")" = "0" ]
}

@test "stdout is always valid JSON array" {
  run bash "$HELPER" --script-exit 0
  [ "$status" -eq 0 ]
  echo "$output" | jq -e 'type == "array"'
}

@test "retry-success: attempt > 1 + completed emits one issue" {
  cat > "$SANDBOX/state.yaml" <<'EOF'
step_history:
  - step_id: task-T-3
    phase: implement
    status: completed
    attempt: 2
EOF
  run bash "$HELPER" --state-yaml "$SANDBOX/state.yaml" --phase implement --step-id task-T-3
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" category)" = "retry-success" ]
  [ "$(_jq_first "$output" dedup_key)" = "retry-success:implement:task-T-3" ]
}

@test "retry-success: attempt == 1 emits nothing" {
  cat > "$SANDBOX/state.yaml" <<'EOF'
step_history:
  - step_id: task-T-3
    phase: implement
    status: completed
    attempt: 1
EOF
  run bash "$HELPER" --state-yaml "$SANDBOX/state.yaml" --phase implement --step-id task-T-3
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "0" ]
}

@test "retry-success: failed status emits nothing" {
  cat > "$SANDBOX/state.yaml" <<'EOF'
step_history:
  - step_id: task-T-3
    phase: implement
    status: failed
    attempt: 3
EOF
  run bash "$HELPER" --state-yaml "$SANDBOX/state.yaml" --phase implement --step-id task-T-3
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "0" ]
}

@test "script-warning: exit 10 + stderr file uses last 5 stderr lines as detail" {
  printf 'l1\nl2\nl3\nl4\nl5\nl6\nl7\n' > "$SANDBOX/se.txt"
  run bash "$HELPER" --script-exit 10 --script-stderr-file "$SANDBOX/se.txt" \
       --phase main --step-id seed-state
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" category)" = "script-warning" ]
  [ "$(_jq_first "$output" dedup_key)" = "script-warning:seed-state" ]
  [ "$(_jq_first "$output" detail)" = "l3"$'\n'"l4"$'\n'"l5"$'\n'"l6"$'\n'"l7" ]
}

@test "script-warning: exit 10 with no stderr file uses default detail" {
  run bash "$HELPER" --script-exit 10 --phase main --step-id seed-state
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" detail)" = "inline script exited 10 (soft-fail)" ]
}

@test "script-warning: exit 0 emits nothing" {
  run bash "$HELPER" --script-exit 0 --phase main --step-id seed-state
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "0" ]
}

@test "script-failed: exit 1 emits one issue" {
  run bash "$HELPER" --script-exit 1 --phase main --step-id seed-state
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" category)" = "script-failed" ]
  [ "$(_jq_first "$output" severity)" = "blocker" ]
  [ "$(_jq_first "$output" dedup_key)" = "script-failed:main:seed-state" ]
}

@test "script-failed: exit 1 + stderr file uses last 5 stderr lines as detail" {
  printf 'a\nb\nc\nd\ne\nf\n' > "$SANDBOX/se-hard.txt"
  run bash "$HELPER" --script-exit 1 --script-stderr-file "$SANDBOX/se-hard.txt" \
       --phase main --step-id seed-state
  [ "$status" -eq 0 ]
  [ "$(_jq_first "$output" detail)" = "b"$'\n'"c"$'\n'"d"$'\n'"e"$'\n'"f" ]
}

@test "tool-crashed: non-zero tool exit emits one issue" {
  run bash "$HELPER" --tool-exit 137 --phase main --step-id explore
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" category)" = "tool-crashed" ]
  [ "$(_jq_first "$output" severity)" = "blocker" ]
  [ "$(_jq_first "$output" dedup_key)" = "tool-crashed:main:explore" ]
}

@test "tool-crashed: zero tool exit emits nothing" {
  run bash "$HELPER" --tool-exit 0 --phase main --step-id explore
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "0" ]
}

@test "retry-success via --attempt: attempt > 1 emits one issue" {
  run bash "$HELPER" --attempt 3 --phase implement --step-id task-T-2
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" category)" = "retry-success" ]
  [ "$(_jq_first "$output" dedup_key)" = "retry-success:implement:task-T-2" ]
}

@test "retry-success via --attempt: attempt == 1 emits nothing" {
  run bash "$HELPER" --attempt 1 --phase implement --step-id task-T-2
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "0" ]
}

@test "manual-phase-advance: flag set emits one issue" {
  run bash "$HELPER" --manual-phase-advance complete
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "1" ]
  [ "$(_jq_first "$output" category)" = "manual-phase-advance" ]
  [ "$(_jq_first "$output" dedup_key)" = "manual-phase-advance:complete" ]
}

@test "combined: tool-exit + manual-phase emits two issues" {
  run bash "$HELPER" --tool-exit 1 --manual-phase-advance main --phase main --step-id sX
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "2" ]
}

@test "missing state.yaml file is non-fatal (empty array)" {
  run bash "$HELPER" --state-yaml "$SANDBOX/does-not-exist.yaml" --phase main --step-id foo
  [ "$status" -eq 0 ]
  [ "$(_jq_len "$output")" = "0" ]
}

@test "unknown flag is ignored (warns on stderr, exits 0)" {
  out=$(bash "$HELPER" --unknown-flag value --tool-exit 1 --phase main --step-id foo 2>/dev/null)
  [ "$(jq -e 'length' <<<"$out")" = "1" ]
}
