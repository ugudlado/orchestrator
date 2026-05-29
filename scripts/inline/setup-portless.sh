#!/usr/bin/env bash
# setup-portless.sh — Configure portless named .localhost dev URL for web projects.
#
# Idempotent: checks if portless is already configured before running.
# Only runs for web projects (next, vite, react, astro, nuxt, svelte, angular).
# Skips for CLI tools, libraries, backend-only services, non-Node projects.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[setup-portless] error: ORCHESTRATOR_REPO_ROOT is not set" >&2
  exit 1
fi

# Detect if this is a web project by inspecting package.json
PKG_JSON="$REPO/package.json"
if [ ! -f "$PKG_JSON" ]; then
  echo "[setup-portless] No package.json found — not a Node project, skipping"
  exit 0
fi

WEB_FRAMEWORKS="next vite react astro nuxt svelte @angular"
IS_WEB=false
for fw in $WEB_FRAMEWORKS; do
  if grep -q "\"$fw" "$PKG_JSON" 2>/dev/null; then
    IS_WEB=true
    break
  fi
done

if [ "$IS_WEB" = "false" ]; then
  echo "[setup-portless] Not a web project — skipping portless setup"
  exit 0
fi

# Check if portless is already configured (dev script has .localhost URL or portless package present)
if grep -q "\.localhost\|portless" "$PKG_JSON" 2>/dev/null; then
  echo "[setup-portless] Portless already configured — skipping"
  exit 0
fi

# Check if portless CLI is available
if ! command -v portless &>/dev/null; then
  echo "[setup-portless] portless CLI not found — skipping (install with: npm i -g portless)" >&2
  # Non-blocking: bootstrap continues
  exit 0
fi

cd "$REPO"
portless setup 2>&1 || {
  echo "[setup-portless] warn: portless setup exited non-zero — continuing bootstrap" >&2
  exit 0
}
echo "[setup-portless] Portless configured"
