#!/usr/bin/env bats
# Inline-script subprocess cwd must anchor to REPO_ROOT, not the invoking cwd (orc-87).
#
# Drives bin/orchestrator next on a canary inline-script step from a foreign cwd.
# No claude stub — inline path runs synchronously inside the CLI.

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
ORCH_BIN="$REPO_ROOT/bin/orchestrator"
FIXTURE_ROOT="$BATS_TMPDIR/inline_cwd_fake_repo"
FOREIGN_ROOT="$BATS_TMPDIR/inline_cwd_foreign"
HOME_DIR=""
TMP_STATE="$BATS_TMPDIR/inline_cwd_state.yaml"

setup() {
  rm -rf "$FIXTURE_ROOT" "$FOREIGN_ROOT"
  mkdir -p "$FIXTURE_ROOT/spec" "$FOREIGN_ROOT"
  HOME_DIR="$FIXTURE_ROOT/.orchestrator"
  mkdir -p "$HOME_DIR/config/steps/canary-write"

  cat > "$FIXTURE_ROOT/spec/project.yaml" <<'YAML'
version: 1
quality_bar:
  max_spawn_failures: 3
  max_retry_rounds: 8
YAML

  cat > "$HOME_DIR/config/steps/canary-write/contract.yaml" <<'YAML'
id: canary-write
version: 1
run: script.sh
YAML

  cat > "$HOME_DIR/config/steps/canary-write/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
# Deliberately unanchored — relies on subprocess cwd binding (orc-87).
mkdir -p spec/changes/CANARY-LEAK
SCRIPT
  chmod +x "$HOME_DIR/config/steps/canary-write/script.sh"

  _write_canary_state "orc-inline-cwd" "$TMP_STATE"
}

teardown() {
  rm -rf "$FIXTURE_ROOT" "$FOREIGN_ROOT"
  rm -f "$TMP_STATE"
}

_write_canary_state() {
  local change_id="$1"
  local out_path="$2"
  python3 - "$change_id" "$FIXTURE_ROOT" "$out_path" <<'PY'
import sys
import yaml
from pathlib import Path

change_id, fake_repo, out_path = sys.argv[1:4]
state = {
    "schema": "feature",
    "change_id": change_id,
    "phase": "main",
    "repo_root": fake_repo,
    "worktree_path": fake_repo,
    "flags": {},
    "status": "active",
    "step_history": [],
    "workflow_plan": {
        "main": {
            "nodes": [
                {
                    "id": "canary-write",
                    "status": "pending",
                    "depends_on": [],
                }
            ],
            "filtered": [],
        }
    },
}
Path(out_path).write_text(yaml.dump(state, sort_keys=False))
PY
}

run_orchestrator_next_from_foreign() {
  run bash -c '
    cd "$1" && \
    ORCHESTRATOR_HOME="$2" \
    "$3" next "$4"
  ' _ "$FOREIGN_ROOT" "$HOME_DIR" "$ORCH_BIN" "$TMP_STATE"
}

@test "inline script from foreign cwd writes under repo_root not foreign cwd" {
  run_orchestrator_next_from_foreign

  [ "$status" -eq 0 ]
  [ ! -d "$FOREIGN_ROOT/spec/changes/CANARY-LEAK" ]
  [ -d "$FIXTURE_ROOT/spec/changes/CANARY-LEAK" ]
}
