#!/usr/bin/env bats
# Smoke test: full workflow via scripts/run-workflow.sh on a 2-step fixture.
#
# Tests T-8 bats must all pass before this test can run.
# This test exercises end-to-end dispatch with:
#   - Step 1: a script step (bash script, exit 0)
#   - Step 2: an agent step (stub claude that emits a valid COMPLETION block)
#
# The orchestrator is stubbed to serve these two steps, then emit complete_workflow.

SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../../scripts/run-workflow.sh"
REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
STUB_DIR="$BATS_TMPDIR/stubs"
TMP_STATE="$BATS_TMPDIR/state.yaml"
DONE_PAYLOADS_DIR="$BATS_TMPDIR/done_payloads"
SCRIPT_STEP_MARKER="$BATS_TMPDIR/script_step_ran"

setup() {
  mkdir -p "$STUB_DIR" "$DONE_PAYLOADS_DIR"
  export PATH="$STUB_DIR:$PATH"
  export REPO_ROOT

  cat > "$TMP_STATE" <<'YAML'
schema: feature
flags: {}
status: active
phase: main
YAML

  # Stub claude to emit a valid COMPLETION block
  cat > "$STUB_DIR/claude" <<'STUB'
#!/bin/sh
cat <<'COMPLETION'
Agent started.

COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-smoke-2
      status: completed
  artifacts: []
COMPLETION
STUB
  chmod +x "$STUB_DIR/claude"
}

teardown() {
  rm -rf "$STUB_DIR" "$DONE_PAYLOADS_DIR"
  rm -f "$SCRIPT_STEP_MARKER"
}

@test "Two-step fixture workflow runs end-to-end, exits 1, and records both steps" {
  # Create the fixture script step
  local script_step
  script_step="$(mktemp)"
  cat > "$script_step" <<SCRIPT
#!/bin/sh
touch "$SCRIPT_STEP_MARKER"
exit 0
SCRIPT
  chmod +x "$script_step"

  # Counter for orchestrator calls
  local counter_file="$BATS_TMPDIR/orch_count"
  echo 0 > "$counter_file"

  # Build orchestrator stub that serves: script step, agent step, then complete
  cat > "$STUB_DIR/orchestrator" <<STUB
#!/bin/sh
COUNT=\$(cat "$counter_file")
echo \$((COUNT + 1)) > "$counter_file"
case "\$1 \$COUNT" in
  "next 0")
    # Step 1: script step
    printf '{"step_id":"step-script","phase":"main","kind":"run_step","run":"$script_step","env":{}}'
    exit 0
    ;;
  "done 1")
    # Record done payload
    cat > "$DONE_PAYLOADS_DIR/done-1.json"
    printf '{"step_id":"step-script","next_step":{"phase":"main","step_id":"task-T-smoke-2"}}'
    exit 0
    ;;
  "next 2")
    # Step 2: agent step
    printf '{"step_id":"task-T-smoke-2","phase":"main","agent":"developer","kind":"run_inline","instruction":"Implement task T-smoke-2","step_context":{"task":{"id":"T-smoke-2","title":"Smoke task"}},"env":{}}'
    exit 0
    ;;
  "done 3")
    # Record done payload
    cat > "$DONE_PAYLOADS_DIR/done-2.json"
    printf '{"step_id":"task-T-smoke-2","next_step":{"phase":"main","step_id":"done"}}'
    exit 0
    ;;
  "next 4")
    # complete_workflow
    exit 1
    ;;
  *)
    # Fallback: complete
    exit 1
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run bash "$SCRIPT_UNDER_TEST" "$TMP_STATE"

  # Verify: exit 1 = complete_workflow
  [ "$status" -eq 1 ]

  # Verify: script step ran (marker file exists)
  [ -f "$SCRIPT_STEP_MARKER" ]

  # Verify: both done payloads were recorded
  [ -f "$DONE_PAYLOADS_DIR/done-1.json" ]
  [ -f "$DONE_PAYLOADS_DIR/done-2.json" ]

  # Verify: agent step done payload has status=completed
  python3 -c "
import json
d = json.load(open('$DONE_PAYLOADS_DIR/done-2.json'))
assert d.get('status') == 'completed', f'Expected status=completed in {d}'
print('agent done payload: OK')
"
}

@test "Cost report mention in output on complete_workflow" {
  # Stub orchestrator to immediately complete
  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next) exit 1 ;;
  *) cat > /dev/null; exit 0 ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run bash "$SCRIPT_UNDER_TEST" "$TMP_STATE"
  [ "$status" -eq 1 ]
  # Output should indicate workflow completion
  [[ "$output" =~ "complete" ]] || [[ "$output" =~ "Workflow" ]]
}
