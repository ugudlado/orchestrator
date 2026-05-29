#!/usr/bin/env bash
# Test: expand-plan end-to-end integration test
#
# T-5 AC-3,AC-4,AC-5,AC-6,AC-7:
#   - happy path appends 3 nodes with correct edges
#   - idempotent rerun is a no-op
#   - cycle file leaves state.yaml unchanged
#   - unknown-id file leaves state.yaml unchanged
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
# Helpers
# ---------------------------------------------------------------------------

make_state() {
  local dir="$1"
  local change_id="$2"
  local spec_dir="$dir/spec/changes/$change_id"
  mkdir -p "$spec_dir"
  cat > "$spec_dir/tasks.yaml" <<'YAML'
version: 1
tasks:
  - id: T-1
    title: Wire X to Y
    files:
      - a.py
    verify:
      - pytest a.py
    depends_on: []
  - id: T-2
    title: Add test
    files:
      - test_a.py
    verify:
      - pytest test_a.py
    depends_on: [T-1]
  - id: T-3
    title: Document
    files:
      - README.md
    verify:
      - echo ok
    depends_on: [T-2]
YAML

  python3 -c "
import yaml, sys
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
        {'id': 'run-phase-review', 'status': 'pending', 'agent': 'reviewer',
         'goal': 'Review', 'inputs': [], 'outputs': ['phase_review_report'],
         'rules': [], 'depends_on': ['expand-plan']},
      ],
      'filtered': [],
    }
  },
  'step_history': [],
}
with open('$dir/state.yaml', 'w') as f:
    yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
"
}

get_node_ids() {
  local state_path="$1"
  python3 -c "
import yaml
with open('$state_path') as f:
    raw = yaml.safe_load(f)
nodes = raw['workflow_plan']['implement']['nodes']
for n in nodes:
    print(n['id'])
"
}

get_rpr_depends_on() {
  local state_path="$1"
  python3 -c "
import yaml
with open('$state_path') as f:
    raw = yaml.safe_load(f)
nodes = raw['workflow_plan']['implement']['nodes']
for n in nodes:
    if n['id'] == 'run-phase-review':
        print(','.join(n.get('depends_on', [])))
        break
"
}

sha256_file() {
  python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"
}

# ---------------------------------------------------------------------------
# Test 1: Happy path — appends 3 nodes with correct edges
# ---------------------------------------------------------------------------

echo "=== Test: expand-plan happy path ==="

TMPDIR1=$(mktemp -d)
trap "rm -rf '$TMPDIR1'" EXIT

make_state "$TMPDIR1" "test-feature"
STATE="$TMPDIR1/state.yaml"

python3 "$ORCHESTRATOR_BIN" expand-plan "$STATE"
check "expand-plan exits 0" $?

NODE_IDS=$(get_node_ids "$STATE")
echo "$NODE_IDS" | grep -q "task-T-1"
check "task-T-1 node present" $?

echo "$NODE_IDS" | grep -q "task-T-2"
check "task-T-2 node present" $?

echo "$NODE_IDS" | grep -q "task-T-3"
check "task-T-3 node present" $?

# Check run-phase-review rewired to last task node
RPR_DEPS=$(get_rpr_depends_on "$STATE")
[[ "$RPR_DEPS" == "task-T-3" ]]
check "run-phase-review.depends_on == [task-T-3]" $?

# Check depends_on chain T-2 → T-1
T2_DEPS=$(python3 -c "
import yaml
with open('$STATE') as f:
    raw = yaml.safe_load(f)
nodes = {n['id']: n for n in raw['workflow_plan']['implement']['nodes']}
print(','.join(nodes['task-T-2'].get('depends_on', [])))
")
[[ "$T2_DEPS" == "task-T-1" ]]
check "task-T-2.depends_on == [task-T-1]" $?

# Check task payload is present
TASK_PAYLOAD=$(python3 -c "
import yaml
with open('$STATE') as f:
    raw = yaml.safe_load(f)
nodes = {n['id']: n for n in raw['workflow_plan']['implement']['nodes']}
task = nodes['task-T-1'].get('task', {})
print(task.get('id', 'MISSING'))
")
[[ "$TASK_PAYLOAD" == "T-1" ]]
check "task-T-1 node carries task payload with id=T-1" $?

# ---------------------------------------------------------------------------
# Test 2: Idempotent rerun
# ---------------------------------------------------------------------------

echo ""
echo "=== Test: expand-plan idempotent rerun ==="

SHA_BEFORE=$(sha256_file "$STATE")
python3 "$ORCHESTRATOR_BIN" expand-plan "$STATE"
check "second expand-plan exits 0" $?
SHA_AFTER=$(sha256_file "$STATE")
[[ "$SHA_BEFORE" == "$SHA_AFTER" ]]
check "second invocation is a no-op (state.yaml byte-identical)" $?

# ---------------------------------------------------------------------------
# Test 3: Cycle file leaves state.yaml unchanged
# ---------------------------------------------------------------------------

echo ""
echo "=== Test: expand-plan cycle detection ==="

TMPDIR2=$(mktemp -d)
trap "rm -rf '$TMPDIR2'" EXIT

CYCLE_CHANGE="test-cycle"
CYCLE_SPEC="$TMPDIR2/spec/changes/$CYCLE_CHANGE"
mkdir -p "$CYCLE_SPEC"

cat > "$CYCLE_SPEC/tasks.yaml" <<'YAML'
version: 1
tasks:
  - id: T-1
    title: A
    files: [a.py]
    verify: [echo ok]
    depends_on: [T-2]
  - id: T-2
    title: B
    files: [b.py]
    verify: [echo ok]
    depends_on: [T-1]
YAML

python3 -c "
import yaml
state = {
  'change_id': '$CYCLE_CHANGE',
  'phase': 'implement',
  'schema': 'feature',
  'repo_root': '$TMPDIR2',
  'worktree_path': '$TMPDIR2',
  'workflow_plan': {
    'implement': {
      'nodes': [
        {'id': 'run-phase-review', 'status': 'pending', 'agent': 'reviewer',
         'goal': '', 'inputs': [], 'outputs': [], 'rules': [], 'depends_on': []},
      ],
      'filtered': [],
    }
  },
  'step_history': [],
}
with open('$TMPDIR2/state.yaml', 'w') as f:
    yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
"

CYCLE_STATE="$TMPDIR2/state.yaml"
SHA_BEFORE_CYCLE=$(sha256_file "$CYCLE_STATE")

python3 "$ORCHESTRATOR_BIN" expand-plan "$CYCLE_STATE" 2>/dev/null
CYCLE_EXIT=$?
[[ "$CYCLE_EXIT" -ne 0 ]]
check "cycle file causes non-zero exit" $?

SHA_AFTER_CYCLE=$(sha256_file "$CYCLE_STATE")
[[ "$SHA_BEFORE_CYCLE" == "$SHA_AFTER_CYCLE" ]]
check "cycle error leaves state.yaml unchanged" $?

# ---------------------------------------------------------------------------
# Test 4: Unknown depends_on leaves state.yaml unchanged
# ---------------------------------------------------------------------------

echo ""
echo "=== Test: expand-plan unknown depends_on ==="

TMPDIR3=$(mktemp -d)
trap "rm -rf '$TMPDIR3'" EXIT

UNKNOWN_CHANGE="test-unknown"
UNKNOWN_SPEC="$TMPDIR3/spec/changes/$UNKNOWN_CHANGE"
mkdir -p "$UNKNOWN_SPEC"

cat > "$UNKNOWN_SPEC/tasks.yaml" <<'YAML'
version: 1
tasks:
  - id: T-1
    title: A
    files: [a.py]
    verify: [echo ok]
    depends_on: [T-99]
YAML

python3 -c "
import yaml
state = {
  'change_id': '$UNKNOWN_CHANGE',
  'phase': 'implement',
  'schema': 'feature',
  'repo_root': '$TMPDIR3',
  'worktree_path': '$TMPDIR3',
  'workflow_plan': {
    'implement': {
      'nodes': [
        {'id': 'run-phase-review', 'status': 'pending', 'agent': 'reviewer',
         'goal': '', 'inputs': [], 'outputs': [], 'rules': [], 'depends_on': []},
      ],
      'filtered': [],
    }
  },
  'step_history': [],
}
with open('$TMPDIR3/state.yaml', 'w') as f:
    yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
"

UNKNOWN_STATE="$TMPDIR3/state.yaml"
SHA_BEFORE_UNKNOWN=$(sha256_file "$UNKNOWN_STATE")

python3 "$ORCHESTRATOR_BIN" expand-plan "$UNKNOWN_STATE" 2>/dev/null
UNKNOWN_EXIT=$?
[[ "$UNKNOWN_EXIT" -ne 0 ]]
check "unknown depends_on causes non-zero exit" $?

SHA_AFTER_UNKNOWN=$(sha256_file "$UNKNOWN_STATE")
[[ "$SHA_BEFORE_UNKNOWN" == "$SHA_AFTER_UNKNOWN" ]]
check "unknown depends_on error leaves state.yaml unchanged" $?

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]; exit $?
