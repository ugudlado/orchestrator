#!/usr/bin/env bats
# Driver-level halt when spawn_failure_cap is exhausted (orc-85 AC-6 / UC-E1).
#
# Uses real bin/orchestrator + scripts/run-workflow.sh; stubs only the tool
# binary (claude) via PATH. Fake repo_root avoids archive_completion matching
# unrelated completed features in this checkout.

SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../../scripts/run-workflow.sh"
REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
STUB_DIR="$BATS_TMPDIR/stubs"
FIXTURE_ROOT="$BATS_TMPDIR/fake_repo"
TMP_STATE="$BATS_TMPDIR/state.yaml"
CLAUDE_COUNTER="$BATS_TMPDIR/claude_invocations"
CLAUDE_INVOKED="$BATS_TMPDIR/claude_invoked"

setup() {
  mkdir -p "$STUB_DIR" "$FIXTURE_ROOT/spec" "$FIXTURE_ROOT/.orchestrator/config/scripts"
  export REPO_ROOT
  export ORCHESTRATOR_DRIVER_SESSION_ID="bats-spawn-failure-halt"
  export PATH="$REPO_ROOT/bin:$STUB_DIR:$PATH"

  cat > "$FIXTURE_ROOT/spec/project.yaml" <<'YAML'
version: 1
quality_bar:
  max_spawn_failures: 3
  max_retry_rounds: 8
YAML

  cp "$REPO_ROOT/config/tools.yaml" "$FIXTURE_ROOT/.orchestrator/config/tools.yaml"
  cat > "$FIXTURE_ROOT/.orchestrator/config/scripts/routes.yaml" <<'YAML'
agents:
  developer: { model: sonnet, subprocess: claude }
models:
  sonnet: claude-sonnet-4-6
YAML

  _write_task_state "bats-spawn-halt" "[]"
}

teardown() {
  rm -rf "$STUB_DIR" "$FIXTURE_ROOT"
  rm -f "$TMP_STATE" "$CLAUDE_COUNTER" "$CLAUDE_INVOKED"
}

# Emit state.yaml with one ready execute-one-task node in main phase.
_write_task_state() {
  local change_id="$1"
  local history_json="$2"
  python3 - "$change_id" "$history_json" "$FIXTURE_ROOT" "$TMP_STATE" <<'PY'
import json, sys
import yaml
from pathlib import Path

change_id, history_json, fake_repo, out_path = sys.argv[1:5]
step_history = json.loads(history_json)
state = {
    "schema": "feature",
    "change_id": change_id,
    "phase": "main",
    "repo_root": fake_repo,
    "worktree_path": fake_repo,
    "flags": {},
    "status": "active",
    "step_history": step_history,
    "workflow_plan": {
        "main": {
            "nodes": [
                {
                    "id": "task-T-spawn",
                    "status": "pending",
                    "agent": "developer",
                    "step_contract": "execute-one-task",
                    "goal": "Spawn failure bats fixture",
                    "inputs": [],
                    "outputs": ["task_execution_result"],
                    "rules": [],
                    "depends_on": [],
                    "task": {
                        "id": "T-spawn",
                        "title": "Spawn failure bats task",
                        "files": [],
                        "verify": ["true"],
                        "depends_on": [],
                    },
                }
            ],
            "filtered": [],
        }
    },
}
Path(out_path).write_text(yaml.dump(state, sort_keys=False))
PY
}

run_workflow() {
  run bash -c 'cd "$1" && bash "$2" "$3"' _ "$FIXTURE_ROOT" "$SCRIPT_UNDER_TEST" "$TMP_STATE"
}

# Stub claude: exit 1 with empty stdout for the first $1 invocations, then success.
_write_claude_stub_fail_then_success() {
  local fail_count="$1"
  echo 0 > "$CLAUDE_COUNTER"
  : > "$CLAUDE_INVOKED"
  cat > "$STUB_DIR/claude" <<STUB
#!/bin/sh
echo 1 >> "$CLAUDE_INVOKED"
COUNT=\$(cat "$CLAUDE_COUNTER")
echo \$((COUNT + 1)) > "$CLAUDE_COUNTER"
if [ "\$COUNT" -lt $fail_count ]; then
  exit 1
fi
cat <<'COMPLETION'
COMPLETION:
  status: completed
  usage:
    input_tokens: 10
    output_tokens: 5
    model: claude-sonnet-4-6
  outputs:
    task_execution_result:
      task_id: T-spawn
      status: completed
  artifacts: []
COMPLETION
STUB
  chmod +x "$STUB_DIR/claude"
}

# Stub claude: always exit 1 with empty stdout (spawn failure signal).
_write_claude_stub_spawn_fail() {
  : > "$CLAUDE_INVOKED"
  cat > "$STUB_DIR/claude" <<STUB
#!/bin/sh
echo 1 >> "$CLAUDE_INVOKED"
exit 1
STUB
  chmod +x "$STUB_DIR/claude"
}

@test "three consecutive spawn failures halt driver with exit 2 and spawn_failure_cap" {
  _write_claude_stub_spawn_fail
  run_workflow

  [ "$status" -eq 2 ]
  [[ "$output" =~ spawn_failure_cap ]]
  [ "$(wc -l < "$CLAUDE_INVOKED" | tr -d ' ')" -eq 3 ]
}

@test "two spawn failures then success completes without exit 2 (boundary)" {
  _write_claude_stub_fail_then_success 2
  run_workflow

  [ "$status" -ne 2 ]
  [ "$status" -eq 1 ]
  [ "$(wc -l < "$CLAUDE_INVOKED" | tr -d ' ')" -eq 3 ]
  [[ "$output" != *spawn_failure_cap* ]]
}

@test "rerun after spawn_failure_cap clears history and retries" {
  _write_claude_stub_spawn_fail
  run_workflow
  [ "$status" -eq 2 ]
  [ "$(wc -l < "$CLAUDE_INVOKED" | tr -d ' ')" -eq 3 ]

  _write_claude_stub_fail_then_success 0
  run_workflow
  [ "$status" -eq 1 ]
  [[ "$output" =~ Resuming\ after\ spawn_failure_cap ]]
  [[ "$output" != *BLOCKED:\ spawn_failure_cap* ]]
}
