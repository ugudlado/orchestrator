#!/usr/bin/env bash
# Test: end-to-end implement phase with flat task-nodes
#
# T-11 AC-11, AC-12, AC-14:
#   (a) Three discrete task-node completions in step_history
#   (b) orchestrator graph Mermaid output contains task_T_1, task_T_2, task_T_3
#   (c) resume: mark task-T-1 completed, orchestrator next returns task-T-2
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ORCHESTRATOR_BIN="$REPO_ROOT/bin/orchestrator"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

make_state_with_task_nodes() {
  local dir="$1"
  local change_id="$2"
  local spec_dir="$dir/spec/changes/$change_id"
  mkdir -p "$spec_dir"

  # Write tasks.yaml
  cat > "$spec_dir/tasks.yaml" <<'YAML'
version: 1
tasks:
  - id: T-1
    title: Wire X to Y
    files:
      - a.py
    verify:
      - echo ok
    depends_on: []
  - id: T-2
    title: Add test
    files:
      - test_a.py
    verify:
      - echo ok
    depends_on: [T-1]
  - id: T-3
    title: Document
    files:
      - README.md
    verify:
      - echo ok
    depends_on: [T-2]
YAML

  # Write initial state with expanded task-nodes already in place
  # (simulates result of expand-plan having been run)
  python3 -c "
import yaml
state = {
  'change_id': '$change_id',
  'phase': 'implement',
  'schema': 'feature',
  'repo_root': '$dir',
  'worktree_path': '$dir',
  'workflow_plan': {
    'implement': {
      'nodes': [
        {'id': 'design-and-draft-artifacts', 'status': 'completed', 'agent': 'architect',
         'goal': 'Generate design', 'inputs': [], 'outputs': ['design.md'], 'rules': []},
        {'id': 'expand-plan', 'status': 'completed', 'agent': 'inline',
         'goal': 'Expand plan', 'inputs': [], 'outputs': [], 'rules': []},
        {'id': 'task-T-1', 'status': 'pending', 'agent': 'developer',
         'step_contract': 'execute-one-task',
         'goal': 'Wire X to Y', 'inputs': [], 'outputs': ['task_execution_result'],
         'rules': [], 'depends_on': ['expand-plan'],
         'task': {'id': 'T-1', 'title': 'Wire X to Y', 'files': ['a.py'],
                  'verify': ['echo ok'], 'depends_on': []}},
        {'id': 'task-T-2', 'status': 'pending', 'agent': 'developer',
         'step_contract': 'execute-one-task',
         'goal': 'Add test', 'inputs': [], 'outputs': ['task_execution_result'],
         'rules': [], 'depends_on': ['task-T-1'],
         'task': {'id': 'T-2', 'title': 'Add test', 'files': ['test_a.py'],
                  'verify': ['echo ok'], 'depends_on': ['T-1']}},
        {'id': 'task-T-3', 'status': 'pending', 'agent': 'developer',
         'step_contract': 'execute-one-task',
         'goal': 'Document', 'inputs': [], 'outputs': ['task_execution_result'],
         'rules': [], 'depends_on': ['task-T-2'],
         'task': {'id': 'T-3', 'title': 'Document', 'files': ['README.md'],
                  'verify': ['echo ok'], 'depends_on': ['T-2']}},
        {'id': 'run-phase-review', 'status': 'pending', 'agent': 'reviewer',
         'goal': 'Review', 'inputs': [], 'outputs': ['phase_review_report'],
         'rules': [], 'depends_on': ['task-T-3']},
      ],
      'filtered': [],
    }
  },
  'step_history': [
    {'step_id': 'design-and-draft-artifacts', 'phase': 'implement', 'status': 'completed',
     'agent': 'architect', 'attempt': 1},
    {'step_id': 'expand-plan', 'phase': 'implement', 'status': 'completed',
     'agent': 'inline', 'attempt': 1},
  ],
}
with open('$dir/state.yaml', 'w') as f:
    yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
"
}

record_task_completion() {
  local state_path="$1"
  local task_id="$2"
  # task-nodes run as developer agents; include minimal token counts to pass
  # the agent_step_missing_usage guard in record.py.
  python3 "$ORCHESTRATOR_BIN" done "$state_path" <<EOF
{
  "step_id": "$task_id",
  "phase": "implement",
  "status": "completed",
  "agent": "developer",
  "outputs": {"task_execution_result": {"task_id": "$task_id", "status": "completed"}},
  "usage": {"input_tokens": 1000, "output_tokens": 200}
}
EOF
}

count_step_history_completions() {
  local state_path="$1"
  python3 -c "
import yaml
with open('$state_path') as f:
    raw = yaml.safe_load(f)
count = sum(
    1 for e in raw.get('step_history', [])
    if e.get('step_id', '').startswith('task-') and e.get('status') == 'completed'
)
print(count)
"
}

get_next_step_id() {
  local state_path="$1"
  python3 "$ORCHESTRATOR_BIN" next "$state_path" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('step_id', data.get('next_step', {}).get('step_id', '')))
" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Test A: Three task-node completions land in step_history
# ---------------------------------------------------------------------------

echo "=== Test A: three task-node completions in step_history ==="

TMPDIR_A=$(mktemp -d)
trap "rm -rf '$TMPDIR_A'" EXIT

make_state_with_task_nodes "$TMPDIR_A" "e2e-feature"
STATE_A="$TMPDIR_A/state.yaml"

# Record completions for all three task-nodes
record_task_completion "$STATE_A" "task-T-1" > /dev/null 2>&1
check "record task-T-1 exits 0" $?
record_task_completion "$STATE_A" "task-T-2" > /dev/null 2>&1
check "record task-T-2 exits 0" $?
record_task_completion "$STATE_A" "task-T-3" > /dev/null 2>&1
check "record task-T-3 exits 0" $?

TASK_COMPLETIONS=$(count_step_history_completions "$STATE_A")
[[ "$TASK_COMPLETIONS" -eq 3 ]]
check "step_history has 3 task-node completed entries" $?

# Verify each task ID appears in step_history
python3 -c "
import yaml, sys
with open('$STATE_A') as f:
    raw = yaml.safe_load(f)
ids = {e['step_id'] for e in raw.get('step_history', []) if e.get('step_id','').startswith('task-')}
expected = {'task-T-1', 'task-T-2', 'task-T-3'}
sys.exit(0 if expected.issubset(ids) else 1)
"
check "step_history contains task-T-1, task-T-2, task-T-3" $?

# ---------------------------------------------------------------------------
# Test B: orchestrator graph Mermaid output contains all task-node ids
# ---------------------------------------------------------------------------

echo "=== Test B: graph Mermaid output contains task-node IDs ==="

TMPDIR_B=$(mktemp -d)
make_state_with_task_nodes "$TMPDIR_B" "e2e-graph"
STATE_B="$TMPDIR_B/state.yaml"

GRAPH_OUTPUT=$(python3 "$ORCHESTRATOR_BIN" graph "$STATE_B" 2>/dev/null || true)

echo "$GRAPH_OUTPUT" | grep -qi "task.T.1"
check "graph output contains task-T-1 (or task_T_1)" $?

echo "$GRAPH_OUTPUT" | grep -qi "task.T.2"
check "graph output contains task-T-2 (or task_T_2)" $?

echo "$GRAPH_OUTPUT" | grep -qi "task.T.3"
check "graph output contains task-T-3 (or task_T_3)" $?

rm -rf "$TMPDIR_B"

# ---------------------------------------------------------------------------
# Test C: Resume — after task-T-1 completed, orchestrator next returns task-T-2
# ---------------------------------------------------------------------------

echo "=== Test C: resume picks up at task-T-2 after task-T-1 completed ==="

TMPDIR_C=$(mktemp -d)
make_state_with_task_nodes "$TMPDIR_C" "e2e-resume"
STATE_C="$TMPDIR_C/state.yaml"

# Mark task-T-1 as completed
record_task_completion "$STATE_C" "task-T-1" > /dev/null 2>&1
check "record task-T-1 for resume test exits 0" $?

NEXT_ID=$(get_next_step_id "$STATE_C")
[[ "$NEXT_ID" == "task-T-2" ]]
check "orchestrator next returns task-T-2 after task-T-1 completed (got: '$NEXT_ID')" $?

rm -rf "$TMPDIR_C"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $pass passed, $fail failed"
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
exit 0
