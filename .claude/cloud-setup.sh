#!/bin/bash
# Cloud-session setup — runs from repo root via the SessionStart hook in settings.json.
# Guarded to cloud sessions only (CLAUDE_CODE_REMOTE=true); a no-op locally.
# Idempotent: skip work that's already done so session resume stays fast.
set -e

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# Orchestrator Python package (CWD is the repo root here — no path guessing).
python -c "import orchestrator_next" 2>/dev/null || pip install -e . >/dev/null

# Backlog MCP server binary (stdio transport, spawned per .mcp.json). Non-critical.
command -v backlog >/dev/null 2>&1 || npm i -g @ugudlado1/backlog >/dev/null 2>&1 || true

# Sandbox proxy drops the backlog host (closes the socket); bypass it for that host.
# Write to ~/.claude_cloud_env and source it from ~/.profile so both interactive and
# non-interactive shells (Claude's Bash tool) pick it up. ~/.bashrc has an early
# `return` for non-interactive shells, so appending there doesn't work.
if [ -n "${BACKLOG_URL:-}" ]; then
  bl_host="${BACKLOG_URL#*://}"; bl_host="${bl_host%%/*}"; bl_host="${bl_host%%:*}"
  envfile="$HOME/.claude_cloud_env"
  if ! grep -q "NO_PROXY.*$bl_host" "$envfile" 2>/dev/null; then
    cat >> "$envfile" <<EOF
export NO_PROXY="\${NO_PROXY:+\$NO_PROXY,}$bl_host"
export no_proxy="\$NO_PROXY"
EOF
  fi
  # Source into ~/.profile (before the interactive guard in ~/.bashrc) so
  # both interactive AND non-interactive shells (like Claude's Bash tool)
  # pick it up.
  grep -q 'claude_cloud_env' ~/.profile 2>/dev/null || \
    printf '\n[ -f "$HOME/.claude_cloud_env" ] && . "$HOME/.claude_cloud_env"\n' >> ~/.profile
fi
