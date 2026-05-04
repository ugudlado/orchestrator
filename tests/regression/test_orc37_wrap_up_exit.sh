#!/usr/bin/env bash
# Regression test — orc-37 FR-2/FR-5
# Asserts that:
#   (a) cost-report.sh with a missing DB exits non-zero (behavioral sanity check)
#   (b) SKILL.md dispatch-loop prose contains the fail-loud sentinel string
#       "do not silently skip" in the complete_workflow branch
#
# This test FAILS on HEAD before the SKILL.md amendment (T-4) because assertion
# (b) greps for a string that does not yet exist. After T-4 it PASSES.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0

ok() {
    echo "PASS: $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "FAIL: $1"
    FAIL=$((FAIL + 1))
}

# ── Assertion (a): cost-report.sh exits non-zero when DB is missing ──────────
echo "--- Assertion (a): cost-report.sh non-zero on missing DB ---"

MISSING_DB="$(mktemp -u "/tmp/test-orc37-missing-XXXXXX.duckdb")"
# Ensure it does not exist
rm -f "$MISSING_DB"

set +e
METRICS_DB="$MISSING_DB" bash "$REPO_ROOT/scripts/cost-report.sh" --change-id test-change 2>/dev/null
A_EXIT=$?
set -e

if [[ $A_EXIT -ne 0 ]]; then
    ok "cost-report.sh exits non-zero ($A_EXIT) when DB is missing"
else
    fail "cost-report.sh returned 0 on missing DB — expected non-zero"
fi

# ── Assertion (b): SKILL.md contains the fail-loud sentinel ──────────────────
echo "--- Assertion (b): SKILL.md fail-loud sentinel present ---"

SKILL_MD="$REPO_ROOT/skills/orchestrate/SKILL.md"

if [[ ! -f "$SKILL_MD" ]]; then
    fail "SKILL.md not found at $SKILL_MD"
else
    SENTINEL="do not silently skip"
    if grep -qF "$SENTINEL" "$SKILL_MD"; then
        ok "SKILL.md contains fail-loud sentinel: \"$SENTINEL\""
    else
        fail "SKILL.md does NOT contain fail-loud sentinel: \"$SENTINEL\" — wrap-up still says 'do not block' (T-4 not applied)"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi

exit 0
