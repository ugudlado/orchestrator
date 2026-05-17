#!/usr/bin/env bash
# setup-claude-md.sh — Create CLAUDE.md and AGENTS.md as pointers to project.yaml.
#
# Idempotent: only creates a file if it does not already exist.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[setup-claude-md] error: ORCHESTRATOR_REPO_ROOT is not set and git rev-parse failed" >&2
  exit 1
fi

POINTER='Read `spec/project.yaml` for all project context — vision, architecture, tech stack, quality bars, rules, gotchas, and learnings.'

write_pointer() {
  local path="$1" heading="$2"
  if [ -f "$path" ]; then
    echo "[bootstrap] $(basename "$path") already exists — skipping"
    return 0
  fi
  printf '# %s\n\n%s\n' "$heading" "$POINTER" > "$path"
  echo "[bootstrap] created $(basename "$path")"
}

write_pointer "$REPO/CLAUDE.md" "CLAUDE.md"
write_pointer "$REPO/AGENTS.md" "AGENTS.md"
