#!/usr/bin/env bats

UPDATE="$BATS_TEST_DIRNAME/../../scripts/ticket-state-update.sh"
TEST_REPO="$BATS_TMPDIR/test-repo"

setup() {
  mkdir -p "$TEST_REPO/spec/changes/foo"
}

teardown() {
  rm -rf "$TEST_REPO"
}

@test "ticket-state-update merges whitelisted keys only" {
  cat > "$TEST_REPO/spec/changes/foo/state.yaml" <<'YAML'
schema: feature
change_id: foo
next_step: explore
flags:
  auto: true
YAML

  run bash -c "printf '%s\n' '{\"ticket_id\":\"task-9\",\"ticket_status\":\"Ready\",\"ticketing\":\"backlog\",\"ticket_rework\":false,\"flags\":{\"rework_from_review\":false},\"next_step\":\"hacked\"}' | bash \"$UPDATE\" \"$TEST_REPO/spec/changes/foo/state.yaml\""
  [ "$status" -eq 0 ]

  run python3 - "$TEST_REPO/spec/changes/foo/state.yaml" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    s = yaml.safe_load(f)
assert s["ticket_id"] == "task-9"
assert s["ticket_status"] == "Ready"
assert s["ticketing"] == "backlog"
assert s["ticket_rework"] is False
assert s["next_step"] == "explore"
assert s["flags"]["auto"] is True
assert s["flags"]["rework_from_review"] is False
assert "ticket_status_checked_at" in s
PY
  [ "$status" -eq 0 ]
}
