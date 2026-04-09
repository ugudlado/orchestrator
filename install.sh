#!/usr/bin/env bash
set -euo pipefail

ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELL_PROFILE="${HOME}/.zshrc"

echo "Installing orchestrator..."

# 1. Export WORKFLOW_HOME (idempotent)
if ! grep -q 'WORKFLOW_HOME' "$SHELL_PROFILE"; then
  echo "" >> "$SHELL_PROFILE"
  echo "# Orchestrator workflow engine" >> "$SHELL_PROFILE"
  echo "export WORKFLOW_HOME=\"$ORCHESTRATOR_DIR\"" >> "$SHELL_PROFILE"
  echo "  Added WORKFLOW_HOME to $SHELL_PROFILE"
else
  echo "  WORKFLOW_HOME already in $SHELL_PROFILE"
fi

# 2. Symlink agents
if [ -L "${HOME}/.claude/agents" ]; then
  echo "  ~/.claude/agents already symlinked"
elif [ -d "${HOME}/.claude/agents" ]; then
  echo "  WARNING: ~/.claude/agents is a real directory -- please back it up and remove it, then re-run"
  exit 1
else
  ln -s "$ORCHESTRATOR_DIR/agents" "${HOME}/.claude/agents"
  echo "  Symlinked ~/.claude/agents -> $ORCHESTRATOR_DIR/agents"
fi

# 3. Symlink skills
if [ -L "${HOME}/.claude/skills" ]; then
  echo "  ~/.claude/skills already symlinked"
elif [ -d "${HOME}/.claude/skills" ]; then
  echo "  WARNING: ~/.claude/skills is a real directory -- please back it up and remove it, then re-run"
  exit 1
else
  ln -s "$ORCHESTRATOR_DIR/skills" "${HOME}/.claude/skills"
  echo "  Symlinked ~/.claude/skills -> $ORCHESTRATOR_DIR/skills"
fi

echo "Done. Run: source $SHELL_PROFILE"
