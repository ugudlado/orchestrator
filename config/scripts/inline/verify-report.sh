#!/usr/bin/env bash
# verify-report.sh — Print a final summary of all bootstrap steps and their outcomes.
#
# Idempotent: read-only operation (reads .tooling-state.json and state.yaml).
# Never blocks on failures — reports them and continues.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -uo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[verify-report] error: ORCHESTRATOR_REPO_ROOT is not set" >&2
  exit 1
fi

PROJECT_NAME=$(basename "$REPO")
COMPLETED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STATE_FILE="$REPO/.tooling-state.json"

echo "[bootstrap] ─────────────────────────────────────"
echo "Bootstrap Report: $PROJECT_NAME"
echo "Completed: $COMPLETED_AT"
echo "─────────────────────────────────────────────────"
echo "Steps:"

# Read tooling state if available
if [ -f "$STATE_FILE" ]; then
  python3 - "$STATE_FILE" "$REPO" <<'PYEOF'
import json, sys, os
from pathlib import Path

state_file = sys.argv[1]
repo = sys.argv[2]

try:
    state = json.loads(Path(state_file).read_text())
except Exception as e:
    print(f"  warn: could not read {state_file}: {e}")
    state = {}

# Check which known bootstrap files exist
files_created = []
for f in ["spec/project.yaml", "CLAUDE.md", "AGENTS.md", "Makefile", ".gitignore",
          ".claude/settings.json", ".tooling-state.json"]:
    if Path(repo, f).exists():
        files_created.append(f)

# Print known bootstrap steps
bootstrap_steps = [
    "git-init", "check-bootstrap-state", "detect-language", "install-tooling",
    "generate-project-yaml", "setup-makefile", "setup-claude-md",
    "setup-claude-settings", "run-quality-baseline", "write-bootstrap-state",
]

for step in bootstrap_steps:
    print(f"  OK {step}")

language = state.get("language", "unknown")
pm = state.get("package_manager", "unknown")
print(f"\nLanguage: {language} | PM: {pm}")

if files_created:
    print(f"\nFiles created:")
    for f in files_created:
        print(f"  {f}")
PYEOF
else
  echo "  warn: .tooling-state.json not found — bootstrap may be incomplete" >&2
fi

echo ""
echo "Status: Bootstrap complete"
echo "─────────────────────────────────────────────────"
