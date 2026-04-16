#!/usr/bin/env bash
set -euo pipefail

# --- Constants ---
ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-${HOME}/.config/orchestrator}"
SHELL_PROFILE="${HOME}/.zshrc"

CLAUDE_DIR="${HOME}/.claude"
CODEX_DIR="${CODEX_HOME:-${HOME}/.codex}"
ANTIGRAVITY_DIR="${HOME}/.gemini/antigravity"

# --- Helpers ---

# safe_ln <source> <target>
# Idempotently symlinks source to target and logs if changed.
safe_ln() {
  local src="$1"
  local dst="$2"
  local display_name="${dst#$HOME/}"

  # Ensure source exists
  [ -e "$src" ] || return 0

  if [ ! -L "$dst" ] || [ "$(readlink "$dst")" != "$src" ]; then
    ln -sf "$src" "$dst"
    echo "  linked $display_name"
  fi
}

# --- Setup Steps ---

setup_env() {
  echo "Setting up environment..."
  if ! grep -q 'ORCHESTRATOR_HOME' "$SHELL_PROFILE"; then
    echo "" >> "$SHELL_PROFILE"
    echo "# Orchestrator workflow engine" >> "$SHELL_PROFILE"
    echo "export ORCHESTRATOR_HOME=\"$ORCHESTRATOR_HOME\"" >> "$SHELL_PROFILE"
    echo "  Added ORCHESTRATOR_HOME to $SHELL_PROFILE"
  else
    echo "  ORCHESTRATOR_HOME already configured in $SHELL_PROFILE"
  fi
}

setup_core() {
  echo "Syncing core config..."
  mkdir -p "$ORCHESTRATOR_HOME"

  # Symlink config directories
  for dir in "$ORCHESTRATOR_DIR/config"/*/; do
    [ -d "$dir" ] || continue
    safe_ln "${dir%/}" "$ORCHESTRATOR_HOME/$(basename "$dir")"
  done

  # Symlink top-level config files
  for file in "$ORCHESTRATOR_DIR/config"/*.yaml; do
    [ -f "$file" ] || continue
    safe_ln "$file" "$ORCHESTRATOR_HOME/$(basename "$file")"
  done

  local dir_count=$(ls -d "$ORCHESTRATOR_HOME"/*/ 2>/dev/null | wc -l | tr -d ' ')
  local file_count=$(ls "$ORCHESTRATOR_HOME"/*.yaml 2>/dev/null | wc -l | tr -d ' ')
  echo "  Config: $dir_count dirs, $file_count files linked to $ORCHESTRATOR_HOME"
}

setup_claude() {
  echo "Syncing Claude Code..."

  # Agents: file-level symlinks
  mkdir -p "${CLAUDE_DIR}/agents"
  [ -L "${CLAUDE_DIR}/agents" ] && rm "${CLAUDE_DIR}/agents" && mkdir -p "${CLAUDE_DIR}/agents"
  for f in "$ORCHESTRATOR_DIR/agents"/*.md; do
    safe_ln "$f" "${CLAUDE_DIR}/agents/$(basename "$f")"
  done

  # Skills: directory-level symlinks
  mkdir -p "${CLAUDE_DIR}/skills"
  [ -L "${CLAUDE_DIR}/skills" ] && rm "${CLAUDE_DIR}/skills" && mkdir -p "${CLAUDE_DIR}/skills"
  for d in "$ORCHESTRATOR_DIR/skills"/*/; do
    safe_ln "${d%/}" "${CLAUDE_DIR}/skills/$(basename "$d")"
  done

  local agent_count=$(ls "${CLAUDE_DIR}/agents"/*.md 2>/dev/null | wc -l | tr -d ' ')
  local skill_count=$(ls -d "${CLAUDE_DIR}/skills"/*/ 2>/dev/null | wc -l | tr -d ' ')
  echo "  Claude: $agent_count agents, $skill_count skills linked"
}

setup_codex() {
  echo "Syncing Codex..."

  # Agents: file-level symlinks
  mkdir -p "${CODEX_DIR}/agents"
  [ -L "${CODEX_DIR}/agents" ] && rm "${CODEX_DIR}/agents" && mkdir -p "${CODEX_DIR}/agents"
  for f in "$ORCHESTRATOR_DIR/agents"/*.md; do
    safe_ln "$f" "${CODEX_DIR}/agents/$(basename "$f")"
  done

  # Skills: directory-level symlinks while preserving Codex-owned entries like .system.
  mkdir -p "${CODEX_DIR}/skills"
  [ -L "${CODEX_DIR}/skills" ] && rm "${CODEX_DIR}/skills" && mkdir -p "${CODEX_DIR}/skills"
  for d in "$ORCHESTRATOR_DIR/skills"/*/; do
    safe_ln "${d%/}" "${CODEX_DIR}/skills/$(basename "$d")"
  done

  local agent_count=$(ls "${CODEX_DIR}/agents"/*.md 2>/dev/null | wc -l | tr -d ' ')
  local skill_count=$(find "${CODEX_DIR}/skills" -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')
  echo "  Codex: $agent_count agents, $skill_count skills linked"
}

setup_global_hub() {
  echo "Syncing global hub (~/.agents)..."
  mkdir -p "${HOME}/.agents"

  # Clean up singular ~/.agent if it exists
  if [ -e "${HOME}/.agent" ]; then
    rm -rf "${HOME}/.agent"
  fi

  # Handle skills directory/symlink migration
  if [ -d "${HOME}/.agents/skills" ] && [ ! -L "${HOME}/.agents/skills" ]; then
    echo "  backing up existing ~/.agents/skills to ~/.agents/skills.bak"
    mv "${HOME}/.agents/skills" "${HOME}/.agents/skills.bak"
  fi

  if [ ! -L "${HOME}/.agents/rules" ] || [ "$(readlink "${HOME}/.agents/rules")" != "$ORCHESTRATOR_DIR/agents" ]; then
    ln -sf "$ORCHESTRATOR_DIR/agents" "${HOME}/.agents/rules"
    echo "  linked ~/.agents/rules -> agents"
  fi

  if [ ! -L "${HOME}/.agents/skills" ] || [ "$(readlink "${HOME}/.agents/skills")" != "$ORCHESTRATOR_DIR/skills" ]; then
    ln -sf "$ORCHESTRATOR_DIR/skills" "${HOME}/.agents/skills"
    echo "  linked ~/.agents/skills -> skills"
  fi
}

setup_tool_antigravity() {
  echo "Syncing Gemini Antigravity hub wiring..."

  # Instructions (Agents)
  local ag_instructions="${ANTIGRAVITY_DIR}/instructions"
  if [ -d "$ag_instructions" ] && [ ! -L "$ag_instructions" ]; then
    rmdir "$ag_instructions" 2>/dev/null || rm -rf "$ag_instructions"
  fi
  if [ ! -L "$ag_instructions" ] || [ "$(readlink "$ag_instructions")" != "${HOME}/.agents/rules" ]; then
    ln -sf "${HOME}/.agents/rules" "$ag_instructions"
    echo "  wired Antigravity instructions -> ~/.agents/rules"
  fi

  # Global Workflows (Skills) - Antigravity requires .md symbols for slash commands
  local ag_workflows="${ANTIGRAVITY_DIR}/global_workflows"
  mkdir -p "$ag_workflows"
  for d in "${HOME}/.agents/skills"/*/; do
    [ -d "$d" ] || continue
    local skill_name="$(basename "$d")"
    safe_ln "${d}SKILL.md" "${ag_workflows}/${skill_name}.md"
  done
}

# --- Main ---

main() {
  echo "Installing orchestrator..."
  setup_env
  setup_core
  setup_claude
  setup_codex
  setup_global_hub
  # setup_tool_antigravity
  echo "Done. Run: source $SHELL_PROFILE"
}

main
