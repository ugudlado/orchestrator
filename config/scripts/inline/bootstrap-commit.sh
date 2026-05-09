#!/usr/bin/env bash
# bootstrap-commit.sh — Stage bootstrap-generated files and create initial git commit.
#
# Idempotent: if repo already has commits and working tree is clean, skips.
# Never uses git add -A. Never amends existing commits.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[bootstrap-commit] error: ORCHESTRATOR_REPO_ROOT is not set" >&2
  exit 1
fi

cd "$REPO"

# If repo already has commits and working tree is clean, skip
if git log --oneline -1 &>/dev/null; then
  STATUS=$(git status --porcelain)
  if [ -z "$STATUS" ]; then
    echo "[bootstrap-commit] Nothing to commit — skipping bootstrap-commit"
    exit 0
  fi
fi

# Verify .gitignore is in place
if [ ! -f "$REPO/.gitignore" ]; then
  echo "[bootstrap-commit] warn: .gitignore not found — proceeding without it" >&2
fi

# Stage well-known bootstrap files (explicit staging, never git add -A)
BOOTSTRAP_FILES=(
  "spec/project.yaml"
  "CLAUDE.md"
  "AGENTS.md"
  "Makefile"
  ".gitignore"
  ".claude/settings.json"
)

for f in "${BOOTSTRAP_FILES[@]}"; do
  if [ -f "$REPO/$f" ]; then
    git add "$REPO/$f"
  fi
done

# Also stage any other untracked files created during bootstrap (check git status)
# Only stage files that are new/modified and not secrets
while IFS= read -r line; do
  status="${line:0:2}"
  filepath="${line:3}"
  # Skip .env files and other potential secrets
  if [[ "$filepath" =~ \.env($|\.) ]]; then
    continue
  fi
  # Stage untracked (?) and modified (M) files
  if [[ "$status" =~ \?\?|\ M|M\  ]]; then
    git add "$REPO/$filepath" 2>/dev/null || true
  fi
done < <(git status --porcelain)

# Check if there's anything staged
if [ -z "$(git diff --cached --name-only)" ]; then
  echo "[bootstrap-commit] Nothing staged — skipping bootstrap-commit"
  exit 0
fi

PROJECT_NAME=$(basename "$REPO")
COMMIT_MSG="chore: bootstrap $PROJECT_NAME repo tooling

Sets up spec/project.yaml, CLAUDE.md, AGENTS.md, Makefile, .gitignore,
and .claude/settings.json.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

git commit -m "$COMMIT_MSG"
HASH=$(git log --oneline -1 | cut -d' ' -f1)
echo "[bootstrap-commit] Initial commit created: $HASH"
