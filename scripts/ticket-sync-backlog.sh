#!/usr/bin/env bash
# ticket-sync-backlog.sh — set Backlog.md task status via CLI
# Usage: ticket-sync-backlog.sh <ticket-id> <target-status> <repo-root>
set -euo pipefail

TICKET_ID="${1:-}"
TARGET_STATUS="${2:-}"
REPO_ROOT="${3:-}"

if [ -z "$TICKET_ID" ] || [ -z "$TARGET_STATUS" ] || [ -z "$REPO_ROOT" ]; then
  echo "Usage: ticket-sync-backlog.sh <ticket-id> <target-status> <repo-root>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"

if ! command -v backlog >/dev/null 2>&1; then
  echo "WARN: backlog CLI not found" >&2
  exit 2
fi

if (cd "$REPO_ROOT" && backlog task edit "$TICKET_ID" -s "$TARGET_STATUS" >/dev/null 2>&1); then
  echo "ticket-sync-backlog: $TICKET_ID -> $TARGET_STATUS" >&2
  exit 0
fi

echo "WARN: backlog task edit failed for $TICKET_ID -> $TARGET_STATUS" >&2
exit 1
