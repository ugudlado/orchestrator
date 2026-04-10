#!/usr/bin/env bash
set -euo pipefail

ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-${HOME}/.config/orchestrator}"
SHELL_PROFILE="${HOME}/.zshrc"

echo "Installing orchestrator..."

# 1. Export ORCHESTRATOR_HOME (idempotent)
if ! grep -q 'ORCHESTRATOR_HOME' "$SHELL_PROFILE"; then
  echo "" >> "$SHELL_PROFILE"
  echo "# Orchestrator workflow engine" >> "$SHELL_PROFILE"
  echo "export ORCHESTRATOR_HOME=\"$ORCHESTRATOR_HOME\"" >> "$SHELL_PROFILE"
  echo "  Added ORCHESTRATOR_HOME to $SHELL_PROFILE"
else
  echo "  ORCHESTRATOR_HOME already in $SHELL_PROFILE"
fi

# 2. Symlink config directories into ORCHESTRATOR_HOME
mkdir -p "$ORCHESTRATOR_HOME"
for config_dir in "$ORCHESTRATOR_DIR/config"/*/; do
  name="$(basename "$config_dir")"
  target="$ORCHESTRATOR_HOME/$name"
  if [ ! -L "$target" ] || [ "$(readlink "$target")" != "${config_dir%/}" ]; then
    ln -sf "${config_dir%/}" "$target"
    echo "  Symlinked $ORCHESTRATOR_HOME/$name"
  fi
done
# Symlink top-level config files (e.g. grammar.yaml, guidelines.yaml)
for config_file in "$ORCHESTRATOR_DIR/config"/*.yaml; do
  [ -f "$config_file" ] || continue
  name="$(basename "$config_file")"
  target="$ORCHESTRATOR_HOME/$name"
  if [ ! -L "$target" ] || [ "$(readlink "$target")" != "$config_file" ]; then
    ln -sf "$config_file" "$target"
    echo "  Symlinked $ORCHESTRATOR_HOME/$name"
  fi
done
echo "  Config: $(ls -d "$ORCHESTRATOR_HOME"/*/ 2>/dev/null | wc -l | tr -d ' ') dirs, $(ls "$ORCHESTRATOR_HOME"/*.yaml 2>/dev/null | wc -l | tr -d ' ') files linked"

# 3. Symlink agents (file-level: one symlink per .md file)
mkdir -p "${HOME}/.claude/agents"
if [ -L "${HOME}/.claude/agents" ]; then
  # Previously a directory symlink — remove it so we can create a real dir
  rm "${HOME}/.claude/agents"
  mkdir -p "${HOME}/.claude/agents"
fi
for agent_file in "$ORCHESTRATOR_DIR/agents"/*.md; do
  name="$(basename "$agent_file")"
  target="${HOME}/.claude/agents/$name"
  if [ ! -L "$target" ] || [ "$(readlink "$target")" != "$agent_file" ]; then
    ln -sf "$agent_file" "$target"
    echo "  Symlinked ~/.claude/agents/$name"
  fi
done
echo "  Agents: $(ls "${HOME}/.claude/agents"/*.md 2>/dev/null | wc -l | tr -d ' ') linked"

# 4. Symlink skills (dir-level per skill: one symlink per skill directory)
mkdir -p "${HOME}/.claude/skills"
if [ -L "${HOME}/.claude/skills" ]; then
  # Previously a directory symlink — remove it so we can create a real dir
  rm "${HOME}/.claude/skills"
  mkdir -p "${HOME}/.claude/skills"
fi
for skill_dir in "$ORCHESTRATOR_DIR/skills"/*/; do
  name="$(basename "$skill_dir")"
  target="${HOME}/.claude/skills/$name"
  if [ ! -L "$target" ] || [ "$(readlink "$target")" != "${skill_dir%/}" ]; then
    ln -sf "${skill_dir%/}" "$target"
    echo "  Symlinked ~/.claude/skills/$name"
  fi
done
echo "  Skills: $(ls -d "${HOME}/.claude/skills"/*/ 2>/dev/null | wc -l | tr -d ' ') linked"

echo "Done. Run: source $SHELL_PROFILE"
