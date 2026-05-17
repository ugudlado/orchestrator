#!/usr/bin/env bash
# check-linear-config.sh — Check ticketing config in spec/project.yaml.
# Informational only — never blocks bootstrap.
#
# Idempotent: read-only operation, safe to re-run.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"
REPO_NAME=$(basename "$REPO")
PROJECT_YAML="$REPO/spec/project.yaml"

if [ ! -f "$PROJECT_YAML" ]; then
  echo "[check-linear-config] spec/project.yaml not found for \"$REPO_NAME\""
  echo "[check-linear-config]   Ticketing: not configured — run /bootstrap to set up spec/project.yaml"
  exit 0
fi

TICKETING=$(grep "^ticketing:" "$PROJECT_YAML" 2>/dev/null | awk '{print $2}' | tr -d '"')

if [ "$TICKETING" = "linear" ]; then
  TEAM_PREFIX=$(grep "team_prefix:" "$PROJECT_YAML" | awk '{print $2}' | tr -d '"')
  PROJECT_ID=$(grep "project_id:" "$PROJECT_YAML" | awk '{print $2}' | tr -d '"')
  echo "[check-linear-config] Ticketing: linear (team: ${TEAM_PREFIX:-unknown}, project: ${PROJECT_ID:-unknown})"
elif [ "$TICKETING" = "backlog" ]; then
  echo "[check-linear-config] Ticketing: backlog"
else
  echo "[check-linear-config] Ticketing: not configured in spec/project.yaml"
  echo "[check-linear-config]   Add 'ticketing: backlog' or 'ticketing: linear' to spec/project.yaml"
fi
