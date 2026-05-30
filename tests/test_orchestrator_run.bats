#!/usr/bin/env bats
# orchestrator run <ticket-id> — CLI entry for shell workflow loop

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
ORCH_RUN="$REPO_ROOT/orchestrator_next/scripts/orchestrator-run.sh"
BIN_ORCH="$REPO_ROOT/bin/orchestrator"

@test "orchestrator-run.sh prints usage without ticket" {
  run bash "$ORCH_RUN" --help
  [ "$status" -eq 7 ]
  [[ "$output" == *"orchestrator run"* ]]
}

@test "bin/orchestrator run delegates to orchestrator-run.sh" {
  run python3 "$BIN_ORCH" run 2>&1
  [ "$status" -eq 7 ]
  [[ "$output" == *"ticket-id"* ]] || [[ "$output" == *"Usage"* ]]
}
