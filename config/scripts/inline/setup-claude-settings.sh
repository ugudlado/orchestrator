#!/usr/bin/env bash
# setup-claude-settings.sh — Write a baseline .claude/settings.json so the
# workflow runs without permission prompts.
#
# Idempotent: if .claude/settings.json already exists, skip entirely
# (never overwrite — the existing file may be hand-tuned).
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[setup-claude-settings] error: ORCHESTRATOR_REPO_ROOT is not set and git rev-parse failed" >&2
  exit 1
fi

SETTINGS="$REPO/.claude/settings.json"

if [ -f "$SETTINGS" ]; then
  echo "[bootstrap] .claude/settings.json already exists — skipping"
  exit 0
fi

mkdir -p "$REPO/.claude"

cat > "$SETTINGS" <<'JSON'
{
  "permissions": {
    "allow": [
      "Bash",
      "Edit(./**)",
      "Write(./**)",
      "Skill",
      "WebFetch",
      "WebSearch",
      "mcp__chrome-devtools",
      "mcp__plugin_context7_context7",
      "mcp__plugin_claude-mem_mcp",
      "mcp__plugin_linear_linear",
      "mcp__drawio",
      "mcp__pal",
      "Edit(~/code/feature_worktrees/**)",
      "Write(~/code/feature_worktrees/**)",
      "Read(~/code/feature_worktrees/**)",
      "Edit(~/.config/spec/**)",
      "Write(~/.config/spec/**)",
      "Read(~/.config/spec/**)",
      "Write(~/.claude/logs/**)",
      "Edit(~/.claude/logs/**)",
      "Read(~/.claude/logs/**)",
      "Edit(~/.config/hooksmith/**)",
      "Write(~/.config/hooksmith/**)",
      "Read(~/.config/hooksmith/**)",
      "Edit(~/.config/linear/**)",
      "Write(~/.config/linear/**)",
      "Read(~/.config/linear/**)"
    ],
    "deny": []
  },
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": true,
    "excludedCommands": [],
    "additionalWritePaths": [
      "~/.config/spec",
      "~/.config/hooksmith",
      "~/.config/linear",
      "~/.claude/logs",
      "~/code/feature_worktrees"
    ]
  },
  "enableAllProjectMcpServers": true
}
JSON

echo "[bootstrap] Generated .claude/settings.json with workflow permissions"
