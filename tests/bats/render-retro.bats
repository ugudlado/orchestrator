#!/usr/bin/env bats
bats_require_minimum_version 1.5.0
# Contract tests for scripts/render-retro.sh (orc-90).
#
# RED (T-1): script absent — _assert_red_missing_script records the expected
#   "scripts/render-retro.sh: No such file or directory" failure; GREEN
#   assertions run once scripts/render-retro.sh exists (T-2).

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
RENDER_SCRIPT="$REPO_ROOT/scripts/render-retro.sh"
FIXTURES_DIR="$BATS_TEST_DIRNAME/fixtures"
FIXTURE_POPULATED="$FIXTURES_DIR/retro-populated.md"
FIXTURE_HEADER_ONLY="$FIXTURES_DIR/retro-header-only.md"
FAKE_ROOT="$BATS_TMPDIR/render_retro_fake_root"

setup() {
  rm -rf "$FAKE_ROOT"
  mkdir -p "$FAKE_ROOT/spec/changes/archive"
  export WORKTREE_ROOT="$FAKE_ROOT"
  export REPO_ROOT="$FAKE_ROOT"
}

teardown() {
  rm -rf "$FAKE_ROOT"
}

_assert_red_missing_script() {
  run -127 env WORKTREE_ROOT="$WORKTREE_ROOT" REPO_ROOT="$REPO_ROOT" \
    bash "$RENDER_SCRIPT" "orc-red-check"
  [ "$status" -eq 127 ]
  [[ "$output" == *"scripts/render-retro.sh: No such file or directory"* ]]
}

_require_renderer() {
  if [[ -f "$RENDER_SCRIPT" ]]; then
    return 0
  fi
  _assert_red_missing_script
  return 1
}

_install_archived_retro() {
  local change_id="$1"
  local source_fixture="$2"
  local dest="$FAKE_ROOT/spec/changes/archive/$change_id"
  mkdir -p "$dest"
  cp "$source_fixture" "$dest/retro.md"
}

_invoke_renderer() {
  local change_id="$1"
  run --separate-stderr env WORKTREE_ROOT="$WORKTREE_ROOT" REPO_ROOT="$REPO_ROOT" \
    bash "$RENDER_SCRIPT" "$change_id"
}

@test "populated retro.md renders issues table on stderr" {
  _require_renderer || return 0

  _install_archived_retro "orc-fixture-populated" "$FIXTURE_POPULATED"
  _invoke_renderer "orc-fixture-populated"

  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"## Issues this run (3)"* ]]
  [[ "$stderr" == *"| Severity | Category | Detail | Fix direction |"* ]]
  [[ "$stderr" == *"|---|---|---|---|"* ]]

  # First issue fields (document order)
  [[ "$stderr" == *"| blocker | missing-contract | Schema lists a step with no contract file on disk. | Add contract validation at workflow init. |"* ]]
  [[ "$stderr" == *"| cosmetic | sandbox-block | Inline script cannot write to /var/folders temp path. | Prefix mktemp with \${TMPDIR:-/tmp}. |"* ]]
  [[ "$stderr" == *"| workaround-applied | dispatch-bug | Driver re-pointed next_step manually between tasks. | Bootstrap constraint for self-referential bugfixes. |"* ]]
}

@test "missing retro.md emits nothing and exits 0" {
  _require_renderer || return 0

  _invoke_renderer "orc-no-archive-dir"

  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "header-only retro.md emits nothing and exits 0" {
  _require_renderer || return 0

  _install_archived_retro "orc-fixture-header-only" "$FIXTURE_HEADER_ONLY"
  _invoke_renderer "orc-fixture-header-only"

  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "malformed issue block renders em dash for missing severity" {
  _require_renderer || return 0

  local change_id="orc-malformed"
  local dest="$FAKE_ROOT/spec/changes/archive/$change_id"
  mkdir -p "$dest"
  cat > "$dest/retro.md" <<'EOF'
# Retro: malformed fixture

## ISSUE-1 — Missing severity bullet
- **category**: other
- **detail**: Block without severity field.
- **fix_direction**: Add severity to payload schema.

## ISSUE-2 — Well formed
- **category**: dispatch-bug
- **severity**: cosmetic
- **detail**: Second row should still render.
- **fix_direction**: No change needed.
EOF

  _invoke_renderer "$change_id"

  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"## Issues this run (2)"* ]]
  [[ "$stderr" == *"| — | other | Block without severity field. | Add severity to payload schema. |"* ]]
  [[ "$stderr" == *"| cosmetic | dispatch-bug | Second row should still render. | No change needed. |"* ]]
}

@test "detail longer than 120 characters is truncated with ellipsis" {
  _require_renderer || return 0

  local long_detail
  long_detail=$(python3 -c 'print("x" * 200)')
  local expected_cell
  expected_cell=$(python3 -c 'print("x" * 120 + "…")')

  local change_id="orc-truncate"
  local dest="$FAKE_ROOT/spec/changes/archive/$change_id"
  mkdir -p "$dest"
  cat > "$dest/retro.md" <<EOF
# Retro: truncation fixture

## ISSUE-1 — Long detail
- **category**: other
- **severity**: cosmetic
- **detail**: ${long_detail}
- **fix_direction**: short fix
EOF

  local detail_len
  detail_len=$(python3 - "$dest/retro.md" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
m = re.search(r"- \*\*detail\*\*:\s*(.+)", text)
print(len(m.group(1)) if m else 0)
PY
)
  [ "$detail_len" -eq 200 ]

  _invoke_renderer "$change_id"

  [ "$status" -eq 0 ]
  [[ "$stderr" == *"| cosmetic | other | ${expected_cell} | short fix |"* ]]
  ! [[ "$stderr" == *"${long_detail}"* ]]
}

@test "renderer is identical under AUTO=true and AUTO=false" {
  _require_renderer || return 0

  _install_archived_retro "orc-auto-compare" "$FIXTURE_POPULATED"

  local stderr_auto_false stderr_auto_true
  stderr_auto_false=$(env WORKTREE_ROOT="$WORKTREE_ROOT" REPO_ROOT="$REPO_ROOT" AUTO=false \
    bash "$RENDER_SCRIPT" "orc-auto-compare" 2>&1 >/dev/null)
  stderr_auto_true=$(env WORKTREE_ROOT="$WORKTREE_ROOT" REPO_ROOT="$REPO_ROOT" AUTO=true \
    bash "$RENDER_SCRIPT" "orc-auto-compare" 2>&1 >/dev/null)

  [ "$stderr_auto_false" = "$stderr_auto_true" ]
  [[ "$stderr_auto_false" == *"## Issues this run (3)"* ]]
}
