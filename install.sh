#!/usr/bin/env bash
set -euo pipefail

# --- Constants ---
ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-${HOME}/.config/orchestrator}"
SHELL_PROFILE="${HOME}/.zshrc"

CLAUDE_DIR="${HOME}/.claude"
CODEX_DIR="${CODEX_HOME:-${HOME}/.codex}"
ANTIGRAVITY_DIR="${HOME}/.gemini/antigravity"
PI_AGENT_DIR="${HOME}/.pi/agent/agents"
PI_SKILLS_DIR="${HOME}/.pi/agent/skills"

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

  # Migrate from legacy flat layout: remove any old per-subdir/per-file symlinks
  # at the install root that point into $ORCHESTRATOR_DIR/config/. The canonical
  # layout is $ORCHESTRATOR_HOME/config -> $ORCHESTRATOR_DIR/config.
  for entry in "$ORCHESTRATOR_HOME"/*; do
    [ -L "$entry" ] || continue
    local target
    target="$(readlink "$entry")"
    case "$target" in
      "$ORCHESTRATOR_DIR/config"/*) rm "$entry"; echo "  removed legacy ${entry#$HOME/}" ;;
    esac
  done

  safe_ln "$ORCHESTRATOR_DIR/config" "$ORCHESTRATOR_HOME/config"
  echo "  Config: $ORCHESTRATOR_HOME/config -> $ORCHESTRATOR_DIR/config"

  safe_ln "$ORCHESTRATOR_DIR/scripts" "$ORCHESTRATOR_HOME/scripts"
  echo "  Scripts: $ORCHESTRATOR_HOME/scripts -> $ORCHESTRATOR_DIR/scripts"

  # ORC-106: orchestrator_next Python package moved to the repo root; expose it
  # under ORCHESTRATOR_HOME so $ORCHESTRATOR_HOME-based PYTHONPATH/sys.path still resolves.
  safe_ln "$ORCHESTRATOR_DIR/orchestrator_next" "$ORCHESTRATOR_HOME/orchestrator_next"
  echo "  Package: $ORCHESTRATOR_HOME/orchestrator_next -> $ORCHESTRATOR_DIR/orchestrator_next"
}

setup_metrics_db() {
  echo "Initializing metrics DB..."
  local db_path="$ORCHESTRATOR_HOME/metrics.duckdb"

  # Idempotent: ensure_schema uses CREATE TABLE IF NOT EXISTS, so
  # re-running on a populated DB is a no-op.
  PYTHONPATH="$ORCHESTRATOR_DIR" \
    python3 -c "
import duckdb
from orchestrator_next.upsert import ensure_schema
db = duckdb.connect('$db_path')
ensure_schema(db)
db.close()
" || {
    echo "  warning: failed to initialize metrics.duckdb at $db_path" >&2
    return 1
  }

  echo "  Metrics DB: $db_path (schema initialized)"
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

setup_git_hooks() {
  echo "Installing git pre-commit hook..."
  local hook_src="$ORCHESTRATOR_DIR/scripts/pre-commit.sh"
  local hook_dst="$ORCHESTRATOR_DIR/.git/hooks/pre-commit"
  [ -f "$hook_src" ] || { echo "  skipped: $hook_src not found"; return 0; }
  [ -d "$ORCHESTRATOR_DIR/.git/hooks" ] || { echo "  skipped: not a git repo"; return 0; }
  safe_ln "$hook_src" "$hook_dst"
}

setup_pi() {
  echo "Syncing Pi coding agent..."

  # ORC-105: pi overrides merged under the `pi:` key in config/agents.yaml;
  # sync_pi_agents.py unwraps it. Legacy config/pi-agents.yaml fallback.
  local pi_config="${ORCHESTRATOR_DIR}/config/agents.yaml"
  [ -f "$pi_config" ] || pi_config="${ORCHESTRATOR_DIR}/config/pi-agents.yaml"
  local project_pi_agents="${ORCHESTRATOR_DIR}/.pi/agents"

  # Agents: generated Pi frontmatter (Claude JSON tools -> Pi comma-separated tools)
  mkdir -p "$PI_AGENT_DIR"
  [ -L "$PI_AGENT_DIR" ] && rm "$PI_AGENT_DIR" && mkdir -p "$PI_AGENT_DIR"
  mkdir -p "$project_pi_agents"
  PYTHONPATH="$ORCHESTRATOR_DIR" \
    python3 "$ORCHESTRATOR_DIR/scripts/sync_pi_agents.py" \
      --source "$ORCHESTRATOR_DIR/agents" \
      --config "$pi_config" \
      --out "$PI_AGENT_DIR" \
      --out "$project_pi_agents"

  # Skills: directory-level symlinks for Pi skill discovery
  mkdir -p "$PI_SKILLS_DIR"
  [ -L "$PI_SKILLS_DIR" ] && rm "$PI_SKILLS_DIR" && mkdir -p "$PI_SKILLS_DIR"
  for d in "$ORCHESTRATOR_DIR/skills"/*/; do
    [ -d "$d" ] || continue
    safe_ln "${d%/}" "${PI_SKILLS_DIR}/$(basename "$d")"
  done

  local agent_count
  agent_count=$(find "$PI_AGENT_DIR" -maxdepth 1 -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  local skill_count
  skill_count=$(find "$PI_SKILLS_DIR" -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')
  echo "  Pi: ${agent_count} agents, ${skill_count} skills linked"
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

setup_python_deps() {
  # orchestrator requires Python 3 for bin/orchestrator and the adapter scripts.
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required but not found on PATH. Install Python 3 before running install.sh." >&2
    exit 1
  fi

  # Gate: only install if any of the three packages is missing.
  if ! python3 -c "import yaml, duckdb, ruamel.yaml" 2>/dev/null; then
    echo "Installing Python dependencies (pyyaml duckdb ruamel.yaml)..."
    pip install --user pyyaml duckdb ruamel.yaml
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
  setup_python_deps
  setup_env
  setup_core
  setup_metrics_db || true
  setup_claude
  setup_codex
  setup_pi
  setup_git_hooks
  setup_global_hub
  # setup_tool_antigravity
  echo "Done. Run: source $SHELL_PROFILE"
}

main
