#!/usr/bin/env bash
# run-quality-baseline.sh — Run all quality gates, auto-fix where possible.
#
# Idempotent: runs gates and reports; no side effects beyond auto-fix.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -uo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[run-quality-baseline] error: ORCHESTRATOR_REPO_ROOT is not set" >&2
  exit 1
fi

cd "$REPO"

# Detect project type
HAS_NODE=false
HAS_PYTHON=false
HAS_RUST=false
HAS_GO=false

[ -f "package.json" ] && HAS_NODE=true
[ -f "pyproject.toml" ] || [ -f "setup.py" ] && HAS_PYTHON=true
[ -f "Cargo.toml" ] && HAS_RUST=true
[ -f "go.mod" ] && HAS_GO=true

echo "[run-quality-baseline] Auto-fix pass..."

# Auto-fix pass
if [ "$HAS_NODE" = "true" ]; then
  pnpm lint --fix 2>/dev/null || npm run lint --if-present -- --fix 2>/dev/null || true
  pnpm format 2>/dev/null || npm run format --if-present 2>/dev/null || true
fi

if [ "$HAS_PYTHON" = "true" ]; then
  ruff check --fix . 2>/dev/null || true
  ruff format . 2>/dev/null || true
fi

if [ "$HAS_RUST" = "true" ]; then
  cargo fmt 2>/dev/null || true
fi

if [ "$HAS_GO" = "true" ]; then
  gofumpt -w . 2>/dev/null || true
fi

echo "[run-quality-baseline] Verification pass..."

# Verification pass — track results
declare -A RESULTS
RESULTS=()

run_gate() {
  local name="$1"
  shift
  if "$@" &>/dev/null; then
    RESULTS["$name"]="pass"
  else
    RESULTS["$name"]="fail"
  fi
}

if [ "$HAS_NODE" = "true" ]; then
  run_gate "lint" pnpm lint 2>/dev/null || run_gate "lint" npm run lint --if-present
  run_gate "format" pnpm format:check 2>/dev/null || RESULTS["format"]="skip"
  run_gate "type-check" pnpm type-check 2>/dev/null || RESULTS["type-check"]="skip"
  run_gate "test" pnpm test 2>/dev/null || RESULTS["test"]="fail"
fi

if [ "$HAS_PYTHON" = "true" ]; then
  run_gate "lint" ruff check . 2>/dev/null || RESULTS["lint"]="fail"
  run_gate "format" ruff format --check . 2>/dev/null || RESULTS["format"]="fail"
  run_gate "test" python3 -m pytest 2>/dev/null || RESULTS["test"]="fail"
fi

# Report baseline
echo "[run-quality-baseline] Quality gate baseline:"
for gate in lint format type-check test; do
  status="${RESULTS[$gate]:-skip}"
  if [ "$status" = "pass" ]; then
    icon="OK"
  elif [ "$status" = "fail" ]; then
    icon="FAIL"
  else
    icon="SKIP"
  fi
  echo "  $gate: $icon"
done
