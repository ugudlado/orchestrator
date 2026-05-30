#!/usr/bin/env bats
bats_require_minimum_version 1.5.0
# Contract tests for orchestrator_next/scripts/workflow/render-retro.sh (orc-90).

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
RENDER_SCRIPT="$REPO_ROOT/orchestrator_next/scripts/workflow/render-retro.sh"
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

_require_renderer() {
  [[ -f "$RENDER_SCRIPT" ]]
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

# --- _emit_feature_rollup integration (run-workflow.sh) ---

_emit_rollup_helpers() {
  local run_workflow="$BATS_TEST_DIRNAME/../../orchestrator_next/scripts/run-workflow.sh"
  export ORCH_SCRIPTS_DIR="$(cd "$BATS_TEST_DIRNAME/../../orchestrator_next/scripts" && pwd)"
  export STATE_INSPECT="${STATE_INSPECT:-$ORCH_SCRIPTS_DIR/lib/state_inspect.py}"
  # shellcheck disable=SC1090
  source <(awk '/^(_log_ts|_log_step_usage|_emit_feature_rollup)\(\) \{/,/^\}/' "$run_workflow")
}

_install_rollup_state() {
  local change_id="$1"
  local tail="${2:-orc-rollup-fixture: \$0.42 · 1m · 2 steps · 1x median}"
  export STATE_YAML="$FAKE_ROOT/state.yaml"
  cat > "$STATE_YAML" <<YAML
change_id: $change_id
step_history:
  - step_id: cost-report
    phase: main
    status: completed
    evidence:
      outputs:
        tail_summary: "$tail"
YAML
}

@test "_emit_feature_rollup prints cost line then issues table on stderr" {
  _require_renderer || return 0

  printf 'version: 1\n' > "$FAKE_ROOT/spec/project.yaml"
  _install_rollup_state "orc-rollup-integration"
  _install_archived_retro "orc-rollup-integration" "$FIXTURE_POPULATED"

  local rollup_stderr
  rollup_stderr=$(
    cd "$FAKE_ROOT" && _emit_rollup_helpers && _emit_feature_rollup "orc-rollup-integration" 2>&1 >/dev/null
  )

  [[ "$rollup_stderr" == *"feature complete:"* ]]
  [[ "$rollup_stderr" == *"orc-rollup-fixture:"* ]]
  [[ "$rollup_stderr" == *"## Issues this run (3)"* ]]
  [[ "$rollup_stderr" == *"| Severity | Category | Detail | Fix direction |"* ]]

  local cost_line issues_heading
  cost_line=$(printf '%s\n' "$rollup_stderr" | grep -n 'feature complete:' | head -1 | cut -d: -f1)
  issues_heading=$(printf '%s\n' "$rollup_stderr" | grep -n '## Issues this run' | head -1 | cut -d: -f1)
  [ -n "$cost_line" ] && [ -n "$issues_heading" ]
  [ "$cost_line" -lt "$issues_heading" ]

  # Only blank lines between cost summary and issues heading.
  local between
  between=$(printf '%s\n' "$rollup_stderr" | sed -n "$((cost_line + 1)),$((issues_heading - 1))p")
  [[ -z "${between//[$'\n\r ']/}" ]]
}

@test "_emit_feature_rollup with no archived retro still prints cost line" {
  _require_renderer || return 0

  printf 'version: 1\n' > "$FAKE_ROOT/spec/project.yaml"
  _install_rollup_state "orc-no-retro-archive"

  local rollup_stderr
  rollup_stderr=$(
    cd "$FAKE_ROOT" && _emit_rollup_helpers && _emit_feature_rollup "orc-no-retro-archive" 2>&1 >/dev/null
  )

  [ "$?" -eq 0 ]
  [[ "$rollup_stderr" == *"feature complete:"* ]]
  [[ "$rollup_stderr" == *"orc-rollup-fixture:"* ]]
  [[ "$rollup_stderr" != *"## Issues this run"* ]]
}

@test "_emit_feature_rollup renders worktree-archived retro via worktree_path" {
  _require_renderer || return 0

  # retro.md lives ONLY under a separate worktree root, not under REPO_ROOT —
  # the renderer must learn that path from STATE_INSPECT workflow-meta.
  local wt_root="$BATS_TMPDIR/render_retro_worktree"
  rm -rf "$wt_root"
  mkdir -p "$wt_root/spec/changes/archive/orc-wt-feature"
  cp "$FIXTURE_POPULATED" "$wt_root/spec/changes/archive/orc-wt-feature/retro.md"

  printf 'version: 1\n' > "$FAKE_ROOT/spec/project.yaml"

  # Stub STATE_INSPECT: emit worktree_path for workflow-meta, swallow the rest.
  local stub="$FAKE_ROOT/state_inspect_stub.py"
  cat > "$stub" <<STUB
import sys
if len(sys.argv) > 1 and sys.argv[1] == "workflow-meta":
    print("worktree_path=$wt_root")
STUB
  export STATE_INSPECT="$stub"
  _install_rollup_state "orc-wt-feature"

  local rollup_stderr
  rollup_stderr=$(
    cd "$FAKE_ROOT" && _emit_rollup_helpers && _emit_feature_rollup "orc-wt-feature" 2>&1 >/dev/null
  )

  rm -rf "$wt_root"
  unset STATE_INSPECT STATE_YAML

  [[ "$rollup_stderr" == *"feature complete:"* ]]
  [[ "$rollup_stderr" == *"## Issues this run (3)"* ]]
}
