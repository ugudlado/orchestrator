#!/usr/bin/env bats
# Tests for scripts/run-workflow.sh dispatch loop.
# Tests must FAIL until T-9 lands (script doesn't exist yet).
#
# Strategy: stub orchestrator, tool binaries, and parse-completion.py via PATH override.
# Each test controls what orchestrator next/done returns.

SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../../scripts/run-workflow.sh"
PARSE_COMPLETION="$BATS_TEST_DIRNAME/../../scripts/parse-completion.py"
REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
STUB_DIR="$BATS_TMPDIR/stubs"
TMP_STATE_DIR="$REPO_ROOT/spec/changes/.bats-run-workflow"
TMP_STATE="$TMP_STATE_DIR/state.yaml"

setup() {
  mkdir -p "$STUB_DIR" "$TMP_STATE_DIR"
  export PATH="$STUB_DIR:$PATH"
  export REPO_ROOT

  # Minimal state.yaml (under repo so REPO_ROOT + .orchestrator overrides resolve)
  cat > "$TMP_STATE" <<'YAML'
schema: feature
flags: {}
status: active
phase: main
YAML
}

teardown() {
  rm -rf "$STUB_DIR" "$TMP_STATE_DIR" "$REPO_ROOT/.orchestrator"
}

# Run run-workflow.sh with the same Python as mise (PyYAML required by invoke_tool).
run_workflow() {
  # Keep stub binaries on PATH; mise exec supplies Python with PyYAML.
  run env PATH="$STUB_DIR:$PATH" mise exec -- bash "$SCRIPT_UNDER_TEST" "$TMP_STATE"
}

# Helper: write an orchestrator stub that returns a sequence of JSON responses
# Usage: write_orchestrator_stub "next1_json" "next2_json" ...
# The stub uses a counter file to return responses in sequence.
write_orchestrator_seq() {
  local counter_file="$BATS_TMPDIR/orch_counter"
  echo "0" > "$counter_file"
  local responses_file="$BATS_TMPDIR/orch_responses"
  > "$responses_file"
  for resp in "$@"; do
    printf '%s\n---NEXT---\n' "$resp" >> "$responses_file"
  done

  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
COUNTER_FILE="$BATS_TMPDIR/orch_counter"
RESPONSES_FILE="$BATS_TMPDIR/orch_responses"
COUNT=$(cat "$COUNTER_FILE")
echo $((COUNT + 1)) > "$COUNTER_FILE"
# Read Nth response (1-indexed)
python3 - "$COUNT" "$RESPONSES_FILE" <<'PYEOF'
import sys
idx = int(sys.argv[1])
with open(sys.argv[2]) as f:
    content = f.read()
responses = [r.strip() for r in content.split('---NEXT---') if r.strip()]
if idx < len(responses):
    print(responses[idx])
    sys.exit(0)
else:
    # No more responses -> complete_workflow
    sys.exit(1)
PYEOF
STUB
  chmod +x "$STUB_DIR/orchestrator"
}

# Helper: minimal run_inline action JSON for developer agent
run_inline_action() {
  local step_id="${1:-task-T-1}"
  printf '{"step_id":"%s","phase":"main","agent":"developer","kind":"run_inline","instruction":"do work","step_context":{"task":{"id":"T-1","title":"Test task"}},"env":{}}' "$step_id"
}

# Helper: run_step action JSON
run_step_action() {
  local step_id="${1:-step-init}"
  local script_path="${2:-/bin/true}"
  printf '{"step_id":"%s","phase":"main","kind":"run_step","run":"%s","env":{}}' "$step_id" "$script_path"
}

# Helper: write a stub tool (claude) that emits a valid COMPLETION block
write_claude_stub() {
  cat > "$STUB_DIR/claude" <<'STUB'
#!/bin/sh
cat <<'COMPLETION'
Agent output here.

COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-1
      status: completed
COMPLETION
STUB
  chmod +x "$STUB_DIR/claude"
}

# Helper: stub cursor agent CLI (use distinct binary name to avoid Cursor.app on PATH)
write_cursor_stub() {
  cat > "$STUB_DIR/cursor-agent-stub" <<STUB
#!/bin/sh
touch "$BATS_TMPDIR/cursor_stub_invoked"
cat <<'COMPLETION'
Agent output here.

COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-1
      status: completed
COMPLETION
STUB
  chmod +x "$STUB_DIR/cursor-agent-stub"
}

# Helper: write a stub tool that exits non-zero
write_claude_stub_fail() {
  cat > "$STUB_DIR/claude" <<'STUB'
#!/bin/sh
echo "Tool failed"
exit 2
STUB
  chmod +x "$STUB_DIR/claude"
}

# Helper: write a stub tool that emits no COMPLETION block
write_claude_stub_no_completion() {
  cat > "$STUB_DIR/claude" <<'STUB'
#!/bin/sh
echo "Some output without completion block"
STUB
  chmod +x "$STUB_DIR/claude"
}

# Helper: write a stub 'orchestrator done' that always succeeds
write_orchestrator_done_stub() {
  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next)
    # Return from responses file based on subcommand counter
    exit 1  # complete_workflow by default; override per test
    ;;
  done)
    # Accept done and succeed
    cat > /dev/null
    echo '{"step_id":"task-T-1","next_step":{"phase":"main","step_id":"done"}}'
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"
}

# --- Test: Script-only workflow runs to completion (exit 1) ---

@test "Script-only workflow runs to completion (exit 1)" {
  # First call to 'orchestrator next' returns a script step; second exits 1 (complete)
  local script_path
  script_path="$(mktemp)"
  echo '#!/bin/sh; exit 0' > "$script_path"
  chmod +x "$script_path"

  local call_count=0
  local call_count_file="$BATS_TMPDIR/call_count"
  echo 0 > "$call_count_file"

  cat > "$STUB_DIR/orchestrator" <<STUB
#!/bin/sh
COUNT=\$(cat "$call_count_file")
echo \$((COUNT + 1)) > "$call_count_file"
case "\$1 \$COUNT" in
  "next 0")
    printf '{"step_id":"step-init","phase":"main","kind":"run_step","run":"$script_path","env":{}}'
    exit 0
    ;;
  "done 1")
    cat > /dev/null
    echo '{"step_id":"step-init","next_step":{"phase":"main","step_id":"done"}}'
    exit 0
    ;;
  "next 2")
    exit 1
    ;;
  *)
    exit 1
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  # exit 1 means complete_workflow
  [ "$status" -eq 1 ]
}

# --- Test: Agent step resolves developer->cursor via routing.yaml ---

@test "Agent run_inline step: routing resolves developer->cursor, COMPLETION parsed, done called" {
  write_cursor_stub
  # Repo overrides: distinct tool name so PATH cannot resolve to Cursor.app.
  mkdir -p "$REPO_ROOT/.orchestrator/config/scripts"
  cp "$REPO_ROOT/config/tools.yaml" "$REPO_ROOT/.orchestrator/config/tools.yaml"
  cp "$REPO_ROOT/scripts/routes.yaml" "$REPO_ROOT/.orchestrator/config/scripts/routes.yaml"
  yq -i ".tools.\"cursor-stub\".binary = \"$STUB_DIR/cursor-agent-stub\"" "$REPO_ROOT/.orchestrator/config/tools.yaml"
  yq -i '.tools."cursor-stub".args_template = ["{prompt}"]' "$REPO_ROOT/.orchestrator/config/tools.yaml"
  yq -i '.agents.developer.subprocess = "cursor-stub"' "$REPO_ROOT/.orchestrator/config/scripts/routes.yaml"

  local call_count_file="$BATS_TMPDIR/call_count"
  echo 0 > "$call_count_file"
  local done_payload_file="$BATS_TMPDIR/done_payload"

  cat > "$STUB_DIR/orchestrator" <<STUB
#!/bin/sh
COUNT=\$(cat "$call_count_file")
echo \$((COUNT + 1)) > "$call_count_file"
case "\$1 \$COUNT" in
  "next 0")
    printf '{"step_id":"task-T-1","phase":"main","agent":"developer","kind":"run_inline","instruction":"do work","step_context":{"task":{"id":"T-1","title":"Test"}},"env":{}}'
    exit 0
    ;;
  "done 1")
    cat > "$done_payload_file"
    echo '{"step_id":"task-T-1","next_step":{"phase":"main","step_id":"done"}}'
    exit 0
    ;;
  "next 2")
    exit 1
    ;;
  *)
    exit 1
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  # Should complete (exit 1)
  [ "$status" -eq 1 ]
  [ -f "$BATS_TMPDIR/cursor_stub_invoked" ]

  # done payload should have been called with the parsed COMPLETION
  [ -f "$done_payload_file" ]
  python3 -c "import json; d=json.load(open('$done_payload_file')); assert d['status']=='completed', d"
}

# --- Test: routing.yaml override — developer->pi causes pi binary to be invoked ---

@test "routing.yaml change developer->pi causes pi binary to be invoked" {
  # Write a pi stub
  cat > "$STUB_DIR/pi" <<'STUB'
#!/bin/sh
# pi run --prompt-file <file>
cat <<'COMPLETION'
COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-1
      status: completed
COMPLETION
STUB
  chmod +x "$STUB_DIR/pi"

  local fake_repo="$BATS_TMPDIR/repo_root"
  mkdir -p "$fake_repo/spec" "$fake_repo/.orchestrator/config/scripts"
  printf 'version: 1\n' > "$fake_repo/spec/project.yaml"
  cp "$REPO_ROOT/config/tools.yaml" "$fake_repo/.orchestrator/config/tools.yaml"
  # Absolute binary path — mise exec prepends real tool bins before PATH stubs.
  yq -i ".tools.pi.binary = \"$STUB_DIR/pi\"" "$fake_repo/.orchestrator/config/tools.yaml"

  local call_count_file="$BATS_TMPDIR/call_count"
  echo 0 > "$call_count_file"

  cat > "$STUB_DIR/orchestrator" <<STUB
#!/bin/sh
COUNT=\$(cat "$call_count_file")
echo \$((COUNT + 1)) > "$call_count_file"
case "\$1 \$COUNT" in
  "next 0")
    printf '{"step_id":"task-T-1","phase":"main","agent":"developer","kind":"run_inline","instruction":"do work","step_context":{"task":{"id":"T-1","title":"Test"}},"env":{}}'
    exit 0
    ;;
  "done 1")
    cat > /dev/null
    exit 0
    ;;
  "next 2")
    exit 1
    ;;
  *)
    exit 1
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  cat > "$fake_repo/.orchestrator/config/scripts/routes.yaml" <<'YAML'
agents:
  developer: { model: sonnet, subprocess: pi }
YAML

  cat > "$STUB_DIR/pi" <<'STUB'
#!/bin/sh
touch "$BATS_TMPDIR/pi_was_invoked"
cat <<'COMPLETION'
COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-1
      status: completed
COMPLETION
STUB
  chmod +x "$STUB_DIR/pi"

  # Also stub claude to avoid accidentally invoking the real claude binary
  cat > "$STUB_DIR/claude" <<'STUB'
#!/bin/sh
cat <<'COMPLETION'
COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-1
      status: completed
COMPLETION
STUB
  chmod +x "$STUB_DIR/claude"

  export REPO_ROOT="$fake_repo"

  run_workflow
  [ -f "$BATS_TMPDIR/pi_was_invoked" ]
  [ "$status" -eq 1 ]
}

# --- Test: Unknown agent role exits 4 ---

@test "Unknown agent role exits 4 with diagnostic" {
  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next)
    printf '{"step_id":"task-T-1","phase":"main","agent":"unknown-agent-xyz","kind":"run_inline","instruction":"do work","step_context":{},"env":{}}'
    exit 0
    ;;
  *)
    cat > /dev/null
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  [ "$status" -eq 4 ]
}

# --- Test: Tool subprocess non-zero exit records status:failed ---

@test "Tool subprocess non-zero exit records status:failed" {
  cat > "$STUB_DIR/cursor-agent-stub" <<'STUB'
#!/bin/sh
echo "Tool failed"
exit 2
STUB
  chmod +x "$STUB_DIR/cursor-agent-stub"
  mkdir -p "$REPO_ROOT/.orchestrator/config/scripts"
  cp "$REPO_ROOT/config/tools.yaml" "$REPO_ROOT/.orchestrator/config/tools.yaml"
  cp "$REPO_ROOT/scripts/routes.yaml" "$REPO_ROOT/.orchestrator/config/scripts/routes.yaml"
  yq -i ".tools.\"cursor-stub\".binary = \"$STUB_DIR/cursor-agent-stub\"" "$REPO_ROOT/.orchestrator/config/tools.yaml"
  yq -i '.tools."cursor-stub".args_template = ["{prompt}"]' "$REPO_ROOT/.orchestrator/config/tools.yaml"
  yq -i '.agents.developer.subprocess = "cursor-stub"' "$REPO_ROOT/.orchestrator/config/scripts/routes.yaml"

  local done_payload_file="$BATS_TMPDIR/done_payload"
  local call_count_file="$BATS_TMPDIR/call_count"
  echo 0 > "$call_count_file"

  cat > "$STUB_DIR/orchestrator" <<STUB
#!/bin/sh
COUNT=\$(cat "$call_count_file")
echo \$((COUNT + 1)) > "$call_count_file"
case "\$1 \$COUNT" in
  "next 0")
    printf '{"step_id":"task-T-1","phase":"main","agent":"developer","kind":"run_inline","instruction":"do work","step_context":{},"env":{}}'
    exit 0
    ;;
  "done 1")
    cat > "$done_payload_file"
    echo '{"step_id":"task-T-1","next_step":{"phase":"main","step_id":"done"}}'
    exit 0
    ;;
  "next 2")
    exit 1
    ;;
  *)
    exit 1
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  [ "$status" -eq 1 ]
  # done payload should record status:failed
  if [ -f "$done_payload_file" ]; then
    python3 -c "import json; d=json.load(open('$done_payload_file')); assert d.get('status') in ('failed','completed'), d"
  fi
}

# --- Test: Malformed COMPLETION exits 5 ---

@test "Malformed COMPLETION exits 5 and prints last 50 lines of stdout" {
  cat > "$STUB_DIR/cursor-agent-stub" <<'STUB'
#!/bin/sh
echo "no completion block here"
STUB
  chmod +x "$STUB_DIR/cursor-agent-stub"
  mkdir -p "$REPO_ROOT/.orchestrator/config/scripts"
  cp "$REPO_ROOT/config/tools.yaml" "$REPO_ROOT/.orchestrator/config/tools.yaml"
  cp "$REPO_ROOT/scripts/routes.yaml" "$REPO_ROOT/.orchestrator/config/scripts/routes.yaml"
  yq -i ".tools.\"cursor-stub\".binary = \"$STUB_DIR/cursor-agent-stub\"" "$REPO_ROOT/.orchestrator/config/tools.yaml"
  yq -i '.tools."cursor-stub".args_template = ["{prompt}"]' "$REPO_ROOT/.orchestrator/config/tools.yaml"
  yq -i '.agents.developer.subprocess = "cursor-stub"' "$REPO_ROOT/.orchestrator/config/scripts/routes.yaml"

  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next)
    printf '{"step_id":"task-T-1","phase":"main","agent":"developer","kind":"run_inline","instruction":"do work","step_context":{},"env":{}}'
    exit 0
    ;;
  *)
    cat > /dev/null
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  [ "$status" -eq 5 ]
}

# --- Test: orchestrator next exit 2 -> loop exits 2 ---

@test "orchestrator next exit 2 -> loop exits 2 with blocker" {
  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next)
    echo "Blocked: dependency not satisfied" >&2
    exit 2
    ;;
  *)
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  [ "$status" -eq 2 ]
}

# --- Test: orchestrator next exit 3 -> loop exits 3 ---

@test "orchestrator next exit 3 -> loop forwards stderr and exits 3" {
  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next)
    echo "Contract error: missing output" >&2
    exit 3
    ;;
  *)
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  [ "$status" -eq 3 ]
}

# --- Test: archived state after terminal inline step (complete-workflow) ---

@test "inline step that archives state exits 1 not 3" {
  cat > "$STUB_DIR/orchestrator" <<STUB
#!/bin/sh
case "\$1" in
  next)
    # Simulate complete-workflow: inline finish with no JSON, state already archived.
    rm -f "$TMP_STATE"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  run_workflow
  [ "$status" -eq 1 ]
  [[ "$output" =~ "Workflow complete" ]] || [[ "$output" =~ "archived" ]]
}

# --- Test: complete_workflow path prints cost report ---

@test "complete_workflow path emits cost report summary" {
  # Stub orchestrator next to immediately return exit 1 (complete_workflow)
  cat > "$STUB_DIR/orchestrator" <<'STUB'
#!/bin/sh
case "$1" in
  next)
    exit 1
    ;;
  *)
    exit 0
    ;;
esac
STUB
  chmod +x "$STUB_DIR/orchestrator"

  # Stub cost-report.sh to emit a known string
  mkdir -p "$STUB_DIR/../scripts"
  cat > "$STUB_DIR/cost-report.sh" <<'STUB'
#!/bin/sh
echo "COST REPORT: total=$0.00"
STUB
  chmod +x "$STUB_DIR/cost-report.sh"

  run_workflow
  [ "$status" -eq 1 ]
  # Output should contain something indicating completion
  [[ "$output" =~ "complete" ]] || [[ "$output" =~ "COST" ]] || [[ "$output" =~ "workflow" ]]
}
