#!/usr/bin/env bash
# Regression test: install.sh DB pre-init + schema + symlink (FR-3 / FR-4 / FR-6)
#
# Assertions:
#   (a) $ORCHESTRATOR_HOME/metrics.duckdb exists after install.sh runs
#   (b) step_events table is queryable in that DB
#   (c) re-running install.sh preserves existing data (idempotency / NFR-1)
#   (d) $ORCHESTRATOR_HOME/scripts/cost-report.sh resolves to the real file
#
# TDD: (a) and (b) FAIL on current HEAD because install.sh does not yet call
# setup_metrics_db().  (c) depends on (a)/(b) passing first — reported as
# SKIP rather than FAIL when the DB was never created.  (d) passes on HEAD
# because the symlink was shipped in d048dc0.
#
# After T-2 (install.sh fix) all four assertions must PASS.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"
ORCHESTRATOR_SCRIPTS_DIR="$REPO_ROOT/config/scripts"

pass=0
fail=0
skip=0

check() {
  local desc="$1"
  local result="$2"   # 0 = pass, non-zero = fail
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

skip_check() {
  local desc="$1"
  local reason="$2"
  echo "SKIP: $desc ($reason)"
  ((skip++))
}

echo "=== Regression test: install.sh metrics DB pre-init (orc-37) ==="
echo "REPO_ROOT=$REPO_ROOT"

# ── Fixtures ──────────────────────────────────────────────────────────────────

# Isolated temp home — prevents install.sh from touching ~/.zshrc, ~/.claude, etc.
TMP_DIR="$(mktemp -d)"
FAKE_HOME="$TMP_DIR/home"
FAKE_ORC_HOME="$TMP_DIR/orc_home"
FAKE_CODEX_HOME="$TMP_DIR/codex"
FAKE_ZSHRC="$FAKE_HOME/.zshrc"
mkdir -p "$FAKE_HOME" "$FAKE_ORC_HOME" "$FAKE_CODEX_HOME"
touch "$FAKE_ZSHRC"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "Temp dir: $TMP_DIR"
echo ""

# ── Run install.sh (first time) ───────────────────────────────────────────────

echo "--- Running install.sh (pass 1) ---"
if HOME="$FAKE_HOME" \
   ORCHESTRATOR_HOME="$FAKE_ORC_HOME" \
   CODEX_HOME="$FAKE_CODEX_HOME" \
   SHELL_PROFILE="$FAKE_ZSHRC" \
   bash "$INSTALL_SH" >"$TMP_DIR/install1.log" 2>&1; then
  echo "install.sh (pass 1) exited 0"
else
  echo "install.sh (pass 1) exited $? (non-zero — may be expected on partial setup)"
fi
echo ""

# ── Assertion (a): metrics.duckdb exists ─────────────────────────────────────

DB_PATH="$FAKE_ORC_HOME/metrics.duckdb"

if [[ -f "$DB_PATH" ]]; then
  check "metrics.duckdb not created by install.sh" 0
else
  check "metrics.duckdb not created by install.sh" 1
fi

# ── Assertion (b): step_events table queryable ────────────────────────────────

if [[ -f "$DB_PATH" ]]; then
  QUERY_RESULT="$(PYTHONPATH="$ORCHESTRATOR_SCRIPTS_DIR" python3 - <<PYEOF 2>/dev/null
import duckdb
db = duckdb.connect('$DB_PATH', read_only=True)
rows = db.execute("SELECT count(*) FROM step_events").fetchall()
db.close()
print(rows[0][0])
PYEOF
  )"
  if [[ "$QUERY_RESULT" == "0" ]]; then
    check "step_events table exists and is queryable after install.sh" 0
  else
    check "step_events table exists and is queryable after install.sh" 1
  fi
else
  skip_check "step_events table exists and is queryable after install.sh" "metrics.duckdb missing (assertion a failed)"
fi

# ── Assertion (c): idempotency — re-running preserves data ───────────────────

if [[ -f "$DB_PATH" ]]; then
  # Insert a marker row so we can verify it survives the second install run.
  INSERT_RC=0
  PYTHONPATH="$ORCHESTRATOR_SCRIPTS_DIR" python3 - <<PYEOF 2>/dev/null || INSERT_RC=$?
import duckdb
db = duckdb.connect('$DB_PATH')
db.execute("""
  INSERT INTO step_events (
    repo_root, change_id, step_id, phase, status,
    attempt, agent_name,
    started_at, ended_at, duration_ms,
    input_tokens, output_tokens, cost_usd,
    model
  ) VALUES (
    '/regression',
    'orc37-regression-marker',
    'test-idempotency',
    'regression',
    'completed',
    1, 'regression-test',
    now(), now(), 0,
    0, 0, 0.0,
    'test'
  )
""")
db.close()
PYEOF

  if [[ "$INSERT_RC" -eq 0 ]]; then
    echo "Marker row inserted for idempotency check"

    # Second install.sh run
    echo "--- Running install.sh (pass 2) ---"
    HOME="$FAKE_HOME" \
    ORCHESTRATOR_HOME="$FAKE_ORC_HOME" \
    CODEX_HOME="$FAKE_CODEX_HOME" \
    SHELL_PROFILE="$FAKE_ZSHRC" \
    bash "$INSTALL_SH" >"$TMP_DIR/install2.log" 2>&1
    echo "install.sh (pass 2) done"
    echo ""

    # Verify marker row survived
    MARKER_COUNT="$(PYTHONPATH="$ORCHESTRATOR_SCRIPTS_DIR" python3 - <<PYEOF 2>/dev/null
import duckdb
db = duckdb.connect('$DB_PATH', read_only=True)
rows = db.execute(
  "SELECT count(*) FROM step_events WHERE change_id = 'orc37-regression-marker'"
).fetchall()
db.close()
print(rows[0][0])
PYEOF
    )"

    if [[ "$MARKER_COUNT" == "1" ]]; then
      check "install.sh idempotency: re-run preserves existing step_events data" 0
    else
      check "install.sh idempotency: re-run preserves existing step_events data" 1
    fi
  else
    skip_check "install.sh idempotency: re-run preserves existing step_events data" "could not insert marker row (step_events table may be missing)"
  fi
else
  skip_check "install.sh idempotency: re-run preserves existing step_events data" "metrics.duckdb missing (assertion a failed)"
fi

# ── Assertion (d): scripts/cost-report.sh resolves via $ORCHESTRATOR_HOME symlink ──

SCRIPTS_LINK="$FAKE_ORC_HOME/scripts"
COST_REPORT_VIA_LINK="$SCRIPTS_LINK/cost-report.sh"
COST_REPORT_REAL="$REPO_ROOT/scripts/cost-report.sh"

if [[ -L "$SCRIPTS_LINK" ]] && [[ -f "$COST_REPORT_VIA_LINK" ]]; then
  # Verify the symlink chain leads to the real file
  RESOLVED="$(cd "$(dirname "$COST_REPORT_VIA_LINK")" && pwd -P)/$(basename "$COST_REPORT_VIA_LINK")"
  EXPECTED="$(cd "$(dirname "$COST_REPORT_REAL")" && pwd -P)/$(basename "$COST_REPORT_REAL")"
  if [[ "$RESOLVED" == "$EXPECTED" ]]; then
    check "\$ORCHESTRATOR_HOME/scripts/cost-report.sh resolves to real file (FR-6)" 0
  else
    echo "  resolved='$RESOLVED'"
    echo "  expected='$EXPECTED'"
    check "\$ORCHESTRATOR_HOME/scripts/cost-report.sh resolves to real file (FR-6)" 1
  fi
else
  if [[ ! -L "$SCRIPTS_LINK" ]]; then
    echo "  $SCRIPTS_LINK is not a symlink"
  elif [[ ! -f "$COST_REPORT_VIA_LINK" ]]; then
    echo "  $COST_REPORT_VIA_LINK not found"
  fi
  check "\$ORCHESTRATOR_HOME/scripts/cost-report.sh resolves to real file (FR-6)" 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: $pass passed, $fail failed, $skip skipped"

if [[ "$fail" -gt 0 ]]; then
  exit 1
else
  exit 0
fi
