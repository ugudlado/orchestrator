#!/usr/bin/env bash
set -euo pipefail

# --- Constants ---
ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-${XDG_CONFIG_HOME:-${HOME}/.config}/orchestrator}"
ORCHESTRATOR_INSTALL_BIN="${ORCHESTRATOR_INSTALL_BIN:-${HOME}/.local/bin}"
SHELL_PROFILE="${HOME}/.zshrc"
if [ -n "${BASH_VERSION:-}" ] && [ -f "${HOME}/.bashrc" ]; then
  SHELL_PROFILE="${HOME}/.bashrc"
fi

CLAUDE_DIR="${HOME}/.claude"
CODEX_DIR="${CODEX_HOME:-${HOME}/.codex}"
PI_DIR="${PI_CODING_AGENT_DIR:-${HOME}/.pi/agent}"
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
  local marker="# Orchestrator workflow engine"
  if ! grep -qF "$marker" "$SHELL_PROFILE" 2>/dev/null; then
    echo "" >> "$SHELL_PROFILE"
    echo "$marker" >> "$SHELL_PROFILE"
    echo "export ORCHESTRATOR_HOME=\"$ORCHESTRATOR_HOME\"" >> "$SHELL_PROFILE"
    # XDG / Debian pattern: prepend user bin dir only when it exists.
    cat >> "$SHELL_PROFILE" <<EOF
if [ -d "$ORCHESTRATOR_INSTALL_BIN" ] ; then
  PATH="$ORCHESTRATOR_INSTALL_BIN:\$PATH"
fi
EOF
    echo "  Added ORCHESTRATOR_HOME and PATH hook to $SHELL_PROFILE"
  else
    grep -q 'ORCHESTRATOR_HOME' "$SHELL_PROFILE" 2>/dev/null \
      || echo "export ORCHESTRATOR_HOME=\"$ORCHESTRATOR_HOME\"" >> "$SHELL_PROFILE"
    if ! grep -qF "$ORCHESTRATOR_INSTALL_BIN" "$SHELL_PROFILE" 2>/dev/null; then
      cat >> "$SHELL_PROFILE" <<EOF
if [ -d "$ORCHESTRATOR_INSTALL_BIN" ] ; then
  PATH="$ORCHESTRATOR_INSTALL_BIN:\$PATH"
fi
EOF
      echo "  Added $ORCHESTRATOR_INSTALL_BIN PATH hook to $SHELL_PROFILE"
    else
      echo "  ORCHESTRATOR_HOME / PATH already configured in $SHELL_PROFILE"
    fi
  fi
}

setup_cli() {
  echo "Installing orchestrator CLI on PATH..."
  local cli_src="$ORCHESTRATOR_DIR/bin/orchestrator"
  local cli_dst="$ORCHESTRATOR_INSTALL_BIN/orchestrator"
  mkdir -p "$ORCHESTRATOR_INSTALL_BIN"
  [ -f "$cli_src" ] || { echo "  skipped: $cli_src not found"; return 0; }
  chmod +x "$cli_src"
  safe_ln "$cli_src" "$cli_dst"
  echo "  orchestrator -> $cli_src"
  if command -v orchestrator >/dev/null 2>&1; then
    echo "  on PATH: $(command -v orchestrator)"
  else
    echo "  run: source $SHELL_PROFILE  (or open a new shell)"
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

  # ORC-106: orchestrator_next Python package moved to the repo root; expose it
  # under ORCHESTRATOR_HOME so $ORCHESTRATOR_HOME-based PYTHONPATH/sys.path still resolves.
  safe_ln "$ORCHESTRATOR_DIR/orchestrator_next" "$ORCHESTRATOR_HOME/orchestrator_next"
  echo "  Package: $ORCHESTRATOR_HOME/orchestrator_next -> $ORCHESTRATOR_DIR/orchestrator_next"

  # Legacy: repo-root scripts/ retired; keep ORCHESTRATOR_HOME/scripts for callers
  # that still use that path (now points at orchestrator_next/scripts).
  if [ -L "$ORCHESTRATOR_HOME/scripts" ]; then
    local scripts_target
    scripts_target="$(readlink "$ORCHESTRATOR_HOME/scripts")"
    case "$scripts_target" in
      "$ORCHESTRATOR_DIR/scripts") rm "$ORCHESTRATOR_HOME/scripts"; echo "  removed legacy ${ORCHESTRATOR_HOME#$HOME/}/scripts symlink" ;;
    esac
  fi
  safe_ln "$ORCHESTRATOR_DIR/orchestrator_next/scripts" "$ORCHESTRATOR_HOME/scripts"
  echo "  Scripts: $ORCHESTRATOR_HOME/scripts -> $ORCHESTRATOR_DIR/orchestrator_next/scripts"
}


setup_claude() {
  echo "Syncing Claude Code..."

  # Skills: directory-level symlinks (agents are now skills)
  mkdir -p "${CLAUDE_DIR}/skills"
  [ -L "${CLAUDE_DIR}/skills" ] && rm "${CLAUDE_DIR}/skills" && mkdir -p "${CLAUDE_DIR}/skills"
  for d in "$ORCHESTRATOR_DIR/skills"/*/; do
    safe_ln "${d%/}" "${CLAUDE_DIR}/skills/$(basename "$d")"
  done

  local skill_count=$(ls -d "${CLAUDE_DIR}/skills"/*/ 2>/dev/null | wc -l | tr -d ' ')
  echo "  Claude: $skill_count skills linked"
}

setup_codex() {
  echo "Syncing Codex..."

  # Skills: directory-level symlinks while preserving Codex-owned entries like .system.
  mkdir -p "${CODEX_DIR}/skills"
  [ -L "${CODEX_DIR}/skills" ] && rm "${CODEX_DIR}/skills" && mkdir -p "${CODEX_DIR}/skills"
  for d in "$ORCHESTRATOR_DIR/skills"/*/; do
    safe_ln "${d%/}" "${CODEX_DIR}/skills/$(basename "$d")"
  done

  local skill_count=$(find "${CODEX_DIR}/skills" -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')
  echo "  Codex: $skill_count skills linked"
}

setup_pi() {
  echo "Syncing Pi coding agent..."

  # Skills: directory-level symlinks for Pi skill discovery
  mkdir -p "${PI_DIR}/skills"
  [ -L "${PI_DIR}/skills" ] && rm "${PI_DIR}/skills" && mkdir -p "${PI_DIR}/skills"
  for d in "$ORCHESTRATOR_DIR/skills"/*/; do
    safe_ln "${d%/}" "${PI_DIR}/skills/$(basename "$d")"
  done

  local skill_count
  skill_count=$(find "${PI_DIR}/skills" -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')
  echo "  Pi: $skill_count skills linked"
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

  # Prefer poetry (uses pyproject.toml lockfile); fall back to pip.
  if command -v poetry >/dev/null 2>&1 && [ -f "$(dirname "$0")/pyproject.toml" ]; then
    echo "Installing Python dependencies via poetry..."
    poetry install --no-interaction --no-root 2>/dev/null || poetry install --no-interaction
  elif ! python3 -c "import yaml, ruamel.yaml, pydantic" 2>/dev/null; then
    echo "Installing Python dependencies (pyyaml ruamel.yaml pydantic)..."
    pip install --user pyyaml ruamel.yaml pydantic
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

# install_config <dest>
# Copy config/ to <dest> and point ORCHESTRATOR_HOME there.
# <dest> can be a path inside a repo (repo-local) or ~/.config/orchestrator (global).
install_config() {
  local dest="${1:-}"
  if [ -z "$dest" ]; then
    echo "Usage: $0 install-config <dest-dir>"
    echo "  Copies config/ to <dest-dir> and sets ORCHESTRATOR_HOME to that path."
    echo "  Examples:"
    echo "    $0 install-config /path/to/myrepo/.orchestrator   # repo-local"
    echo "    $0 install-config ~/.config/orchestrator          # global (default install)"
    exit 1
  fi

  local abs_dest
  abs_dest="$(mkdir -p "$dest" && cd "$dest" && pwd)"

  echo "Copying config to $abs_dest ..."
  cp -r "$ORCHESTRATOR_DIR/config/." "$abs_dest/config"
  echo "  config/ -> $abs_dest/config"

  # Write ORCHESTRATOR_HOME into the shell profile if not already pointing there.
  local marker="# Orchestrator workflow engine"
  if ! grep -qF "ORCHESTRATOR_HOME=\"$abs_dest\"" "$SHELL_PROFILE" 2>/dev/null; then
    echo "" >> "$SHELL_PROFILE"
    echo "$marker" >> "$SHELL_PROFILE"
    echo "export ORCHESTRATOR_HOME=\"$abs_dest\"" >> "$SHELL_PROFILE"
    echo "  Set ORCHESTRATOR_HOME=$abs_dest in $SHELL_PROFILE"
  else
    echo "  ORCHESTRATOR_HOME already set to $abs_dest in $SHELL_PROFILE"
  fi

  echo "Done. Run: source $SHELL_PROFILE"
  echo "Config lives at: $abs_dest/config"
  echo "Edit workflows: $abs_dest/config/workflows/"
  echo "Edit steps:     $abs_dest/config/steps/"
}

# --- Main ---

main() {
  echo "Installing orchestrator..."
  setup_python_deps
  setup_cli
  setup_env
  setup_core
  setup_claude
  setup_codex
  setup_pi
  setup_global_hub
  # setup_tool_antigravity
  echo "Done. Open a new shell or run: source $SHELL_PROFILE"
  echo "Then: orchestrator --help"
}

case "${1:-}" in
  install-config) install_config "${2:-}" ;;
  *)              main ;;
esac
