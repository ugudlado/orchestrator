#!/usr/bin/env bats
# End-to-end: detect-workflow-issues.sh → append-retro.sh (orc-89 refactor path).
# Replaces sentinel/record-issue.sh as the live emit proof for AC-6.

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
HELPER="$REPO_ROOT/scripts/lib/detect-workflow-issues.sh"
APPEND_RETRO="$REPO_ROOT/config/scripts/inline/append-retro.sh"
CHANGE_ID="orc-89-emit-path-bats"

setup() {
  SANDBOX="$BATS_TMPDIR/wfi-emit-$$-$RANDOM"
  RETRO_DIR="$SANDBOX/spec/changes/$CHANGE_ID"
  mkdir -p "$RETRO_DIR"
  export WORKTREE_PATH="$SANDBOX"
  export CHANGE_ID
}

teardown() {
  rm -rf "$SANDBOX"
}

@test "exit-10 detect → append-retro writes one ISSUE block with dedup_key" {
  printf 'soft-fail line 1\nsoft-fail line 2\n' > "$SANDBOX/script_stderr.txt"
  ISSUES_JSON=$(bash "$HELPER" \
    --script-exit 10 \
    --script-stderr-file "$SANDBOX/script_stderr.txt" \
    --phase main \
    --step-id emit-path-test)
  [ "$(jq -e 'length' <<<"$ISSUES_JSON")" = "1" ]

  export ISSUES_JSON
  run bash "$APPEND_RETRO"
  [ "$status" -eq 0 ]
  [ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["appended"])' <<<"$output")" = "1" ]

  RETRO_FILE="$RETRO_DIR/retro.md"
  [ -f "$RETRO_FILE" ]
  grep -qE '^## ISSUE-[0-9]+' "$RETRO_FILE"
  grep -q 'script-warning:emit-path-test' "$RETRO_FILE"
  grep -qF -- '- **category**: script-warning' "$RETRO_FILE"
  grep -q 'soft-fail line 2' "$RETRO_FILE"
}
