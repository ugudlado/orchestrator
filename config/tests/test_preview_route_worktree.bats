#!/usr/bin/env bats
# preview-route worktree + non-worktree dispatch (RED until T-8).
#
# Worktree layout mirrors dispatch: ORCHESTRATOR_WORKFLOW_DIR is the worktree
# root; state.yaml lives at <wt>/spec/changes/<id>/state.yaml.

ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
PREVIEW_ROUTE="$ORCHESTRATOR_ROOT/config/steps/preview-route/script.sh"
CHANGE_ID="orc-fixture"

setup() {
  TEST_REPO="$BATS_TMPDIR/test-repo-$$"
  WT_BASE="$BATS_TMPDIR/wt/$CHANGE_ID"
  STATE_DIR="$WT_BASE/spec/changes/$CHANGE_ID"
  NON_WT_STATE_DIR="$TEST_REPO/spec/changes/$CHANGE_ID"
  FAKE_ORCH="$BATS_TMPDIR/fake-orch-$$"
  ESTIMATOR_CALLS_LOG="$BATS_TMPDIR/estimator_calls-$$.log"

  mkdir -p "$TEST_REPO/spec/changes" "$FAKE_ORCH/config/scripts"
  : >"$ESTIMATOR_CALLS_LOG"

  export TEST_REPO WT_BASE STATE_DIR NON_WT_STATE_DIR FAKE_ORCH ESTIMATOR_CALLS_LOG
  export ORCHESTRATOR_HOME="$FAKE_ORCH"

  write_estimator_stub
  write_live_state "$STATE_DIR"
  write_live_state "$NON_WT_STATE_DIR"
}

teardown() {
  rm -rf "$TEST_REPO" "$BATS_TMPDIR/wt" "$FAKE_ORCH" "$ESTIMATOR_CALLS_LOG"
}

write_estimator_stub() {
  cat >"$FAKE_ORCH/config/scripts/estimate-cost.sh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "${ESTIMATOR_CALLS_LOG:?}"
# Mirror estimate-cost.sh: require a state-dir (…/spec/changes/<id>), not the worktree root.
state_dir="${1:?}"
state_file="$state_dir/state.yaml"
if [ ! -f "$state_file" ]; then
  echo "ERROR: state.yaml not found at $state_file" >&2
  exit 1
fi
cat <<'YAML'
route_preview:
  status: ok
  total_cost_usd: 1.23
YAML
exit 0
STUB
  chmod +x "$FAKE_ORCH/config/scripts/estimate-cost.sh"
}

write_live_state() {
  local state_dir="$1"
  mkdir -p "$state_dir"
  cat >"$state_dir/state.yaml" <<YAML
change_id: $CHANGE_ID
schema: feature
repo_root: $TEST_REPO
YAML
  cat >"$state_dir/tasks.yaml" <<'YAML'
tasks:
  - [T-1, "sample task"]
YAML
}

abs_path() {
  local p="$1" dir base
  dir="$(dirname "$p")"
  base="$(basename "$p")"
  echo "$(cd "$dir" && pwd)/$base"
}

route_preview_status() {
  python3 -c '
import json, sys
text = sys.stdin.read().strip()
line = text.splitlines()[-1] if text else ""
obj = json.loads(line) if line else {}
print(obj.get("route_preview", {}).get("status", ""))
'
}

run_preview_route() {
  : >"$ESTIMATOR_CALLS_LOG"
  # shellcheck disable=SC2068
  run env "$@" bash "$PREVIEW_ROUTE"
}

@test "worktree dispatch: route_preview status is not estimate_unavailable" {
  export ORCHESTRATOR_CHANGE_ID="$CHANGE_ID"
  export ORCHESTRATOR_WORKFLOW_DIR="$WT_BASE"
  export REPO_ROOT="$TEST_REPO"
  export WORKTREE_ROOT="$WT_BASE"
  export WORKFLOW_STATE_DIR="$WT_BASE/spec/changes"

  run_preview_route
  [ "$status" -eq 0 ]

  rp_status="$(printf '%s' "$output" | route_preview_status)"
  [ "$rp_status" != "estimate_unavailable" ]
}

@test "worktree dispatch: estimator receives per-change state-dir, not worktree root" {
  export ORCHESTRATOR_CHANGE_ID="$CHANGE_ID"
  export ORCHESTRATOR_WORKFLOW_DIR="$WT_BASE"
  export REPO_ROOT="$TEST_REPO"
  export WORKTREE_ROOT="$WT_BASE"
  export WORKFLOW_STATE_DIR="$WT_BASE/spec/changes"

  run_preview_route
  [ "$status" -eq 0 ]

  grep -qxF "$(abs_path "$STATE_DIR")" "$ESTIMATOR_CALLS_LOG"
  ! grep -qxF "$(abs_path "$WT_BASE")" "$ESTIMATOR_CALLS_LOG"
}

@test "non-worktree dispatch: route_preview status is not estimate_unavailable" {
  unset ORCHESTRATOR_CHANGE_ID WORKTREE_ROOT WORKFLOW_STATE_DIR
  export ORCHESTRATOR_WORKFLOW_DIR="$NON_WT_STATE_DIR"
  export REPO_ROOT="$TEST_REPO"

  run_preview_route
  [ "$status" -eq 0 ]

  rp_status="$(printf '%s' "$output" | route_preview_status)"
  [ "$rp_status" != "estimate_unavailable" ]
}

@test "non-worktree dispatch: estimator receives workflow dir (state-dir pass-through)" {
  unset ORCHESTRATOR_CHANGE_ID WORKTREE_ROOT WORKFLOW_STATE_DIR
  export ORCHESTRATOR_WORKFLOW_DIR="$NON_WT_STATE_DIR"
  export REPO_ROOT="$TEST_REPO"

  run_preview_route
  [ "$status" -eq 0 ]

  grep -qxF "$(abs_path "$NON_WT_STATE_DIR")" "$ESTIMATOR_CALLS_LOG"
}
