#!/usr/bin/env bats
# append-retro.sh dedup_key skip (orc-89 T-2 RED).
# Expect dedup failures until append-retro.sh gains skip logic (T-4).

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
APPEND_RETRO="$REPO_ROOT/config/scripts/inline/append-retro.sh"
CHANGE_ID="orc-89-dedup-bats"

setup() {
  SANDBOX="$BATS_TMPDIR/append-retro-$$"
  RETRO_DIR="$SANDBOX/spec/changes/$CHANGE_ID"
  mkdir -p "$RETRO_DIR"
  RETRO_FILE="$RETRO_DIR/retro.md"
  export WORKTREE_PATH="$SANDBOX"
  export CHANGE_ID
}

teardown() {
  rm -rf "$SANDBOX"
}

_json_appended() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)["appended"])' <<<"$1"
}

_seed_retro_with_dedup_key() {
  local key="$1"
  cat > "$RETRO_FILE" <<EOF
# Retro: workflow issues surfaced during $CHANGE_ID

## ISSUE-1 — seeded
- **category**: telemetry
- **severity**: workaround-applied
- **surfaced_at**: implement/task-T-1
- **recorded_at**: 2026-05-26T00:00:00Z
- **dedup_key**: $key

EOF
}

@test "append-retro.sh skips issue when dedup_key already in retro.md" {
  _seed_retro_with_dedup_key "dup-key-existing"

  export ISSUES_JSON='[{"title":"duplicate attempt","category":"telemetry","severity":"cosmetic","detail":"should not append","dedup_key":"dup-key-existing"}]'

  run bash "$APPEND_RETRO"
  [ "$status" -eq 0 ]

  appended="$(_json_appended "$output")"
  [ "$appended" -eq 0 ]

  [ "$(grep -cE '^## ISSUE-' "$RETRO_FILE")" -eq 1 ]
}

@test "append-retro.sh appends issue with fresh dedup_key and reports appended 1" {
  _seed_retro_with_dedup_key "existing-key"

  export ISSUES_JSON='[{"title":"new anomaly","category":"tooling-bug","severity":"workaround-applied","detail":"fresh","dedup_key":"brand-new-key"}]'

  run bash "$APPEND_RETRO"
  [ "$status" -eq 0 ]

  appended="$(_json_appended "$output")"
  [ "$appended" -eq 1 ]

  grep -qE '^## ISSUE-[0-9]+' "$RETRO_FILE"
  grep -F -- '- **dedup_key**: brand-new-key' "$RETRO_FILE"
}

@test "append-retro.sh without dedup_key still appends (no skip)" {
  cat > "$RETRO_FILE" <<EOF
# Retro: workflow issues surfaced during $CHANGE_ID

EOF

  export ISSUES_JSON='[{"title":"no dedup key","category":"other","severity":"cosmetic","detail":"always append"}]'

  run bash "$APPEND_RETRO"
  [ "$status" -eq 0 ]

  appended="$(_json_appended "$output")"
  [ "$appended" -eq 1 ]

  grep -q 'no dedup key' "$RETRO_FILE"
  ! grep -q 'dedup_key' "$RETRO_FILE" || true
}
