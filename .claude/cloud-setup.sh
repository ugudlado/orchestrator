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
# export here would die with this hook subprocess, so persist into ~/.bashrc instead —
# new shells source it, run the export in their OWN process, and append (not clobber)
# whatever NO_PROXY the harness already set. Host derived from BACKLOG_URL so it can't
# drift. ponytail: strip scheme+port with shell expansion (no URL parser); grep guard
# keeps it idempotent across session resumes.
if [ -n "${BACKLOG_URL:-}" ]; then
  bl_host="${BACKLOG_URL#*://}"; bl_host="${bl_host%%/*}"; bl_host="${bl_host%%:*}"
  grep -q "NO_PROXY.*$bl_host" ~/.bashrc 2>/dev/null || cat >> ~/.bashrc <<EOF
export NO_PROXY="\${NO_PROXY:+\$NO_PROXY,}$bl_host"
export no_proxy="\$NO_PROXY"
EOF
fi
