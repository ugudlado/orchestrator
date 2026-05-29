#!/usr/bin/env bash
# ORC-18 T-4 (RED) / T-5 (GREEN): make doctor and python -m orchestrator_next.doctor
# must share the same entry point; skills/doctor/SKILL.md must shell out to it.
#
# GREEN assertions (full output match modulo timestamps, skill file) pass after T-5.
# Before T-5, RED preconditions confirm the legacy Makefile recipe and missing skill.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SKILL_PATH="$REPO_ROOT/skills/doctor/SKILL.md"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
export REPO_ROOT
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

normalize_output() {
  sed -E \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+-Z]*/<timestamp>/g' \
    -e 's/[0-9]+d ago/<age>/g'
}

capture_make_doctor() {
  make -C "$REPO_ROOT" doctor 2>&1 | grep -v '^make: \*\*\*' || true
}

capture_python_doctor() {
  python3 -m orchestrator_next.doctor 2>&1 || true
}

run_green_assertions() {
  local local_fail=0
  PASS=0
  FAIL=0

  local make_out py_out make_norm py_norm make_first py_first
  make_out="$(capture_make_doctor)"
  py_out="$(capture_python_doctor)"

  if [ -n "$make_out" ]; then pass "make doctor output non-empty"; else fail "make doctor output non-empty"; local_fail=1; fi
  if [ -n "$py_out" ]; then pass "python -m orchestrator_next.doctor output non-empty"; else fail "python -m orchestrator_next.doctor output non-empty"; local_fail=1; fi

  make_norm="$(printf '%s\n' "$make_out" | normalize_output)"
  py_norm="$(printf '%s\n' "$py_out" | normalize_output)"
  if [ "$make_norm" = "$py_norm" ]; then
    pass "make doctor matches python -m orchestrator_next.doctor (modulo timestamps)"
  else
    fail "make doctor matches python -m orchestrator_next.doctor (modulo timestamps)"
    local_fail=1
  fi

  make_first="$(printf '%s\n' "$make_out" | head -1)"
  py_first="$(printf '%s\n' "$py_out" | head -1)"
  if [ "$make_first" = "$py_first" ]; then
    pass "identical first-line output"
  else
    fail "identical first-line output (make: '$make_first' vs python: '$py_first')"
    local_fail=1
  fi

  if [ -f "$SKILL_PATH" ]; then pass "skills/doctor/SKILL.md exists"; else fail "skills/doctor/SKILL.md exists"; local_fail=1; fi
  if [ -f "$SKILL_PATH" ] && grep -qE 'make doctor|orchestrator doctor|orchestrator_next\.doctor' "$SKILL_PATH"; then
    pass "skill invokes doctor entry point"
  else
    fail "skill invokes doctor entry point"
    local_fail=1
  fi

  if [ "$local_fail" -ne 0 ]; then
    return 1
  fi
  return 0
}

run_red_preconditions() {
  PASS=0
  FAIL=0

  local make_out py_out make_first
  make_out="$(capture_make_doctor)"
  py_out="$(capture_python_doctor)"
  make_first="$(printf '%s\n' "$make_out" | head -1)"

  if printf '%s' "$make_first" | grep -q 'Checking orchestrator health'; then
    pass "RED: make doctor still uses legacy shell recipe"
  else
    fail "RED: make doctor still uses legacy shell recipe"
  fi

  if [ "$make_out" != "$py_out" ]; then
    pass "RED: make doctor and python -m outputs differ (not wired yet)"
  else
    fail "RED: make doctor and python -m outputs differ (not wired yet)"
  fi

  if [ ! -f "$SKILL_PATH" ]; then
    pass "RED: skills/doctor/SKILL.md absent until T-5"
  else
    fail "RED: skills/doctor/SKILL.md absent until T-5"
  fi

  test "$FAIL" -eq 0
}

if run_green_assertions; then
  echo ""
  echo "Surface wiring GREEN (T-5+ complete): $PASS checks passed"
  exit 0
fi

echo ""
echo "GREEN assertions not met ($FAIL failed) — checking RED preconditions for T-4..."

if run_red_preconditions; then
  echo ""
  echo "RED preconditions satisfied (wire make doctor + skill in T-5): $PASS checks passed"
  exit 0
fi

echo ""
echo "Results: $PASS passed, $FAIL failed — unexpected state (not GREEN or expected RED)"
exit 1
