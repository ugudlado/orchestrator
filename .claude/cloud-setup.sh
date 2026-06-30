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

# The sandbox proxy drops the backlog host (closes the socket); bypass it for that host.
# Host is derived from BACKLOG_URL so it can't drift. ponytail: strips scheme+port, no URL parser.
if [ -n "${BACKLOG_URL:-}" ]; then
  bl_host="${BACKLOG_URL#*://}"; bl_host="${bl_host%%/*}"; bl_host="${bl_host%%:*}"
  export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$bl_host"
  export no_proxy="$NO_PROXY"
fi
