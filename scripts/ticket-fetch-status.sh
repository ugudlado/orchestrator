#!/usr/bin/env bash
# ticket-fetch-status.sh — fetch current ticket status (router)
# Usage: ticket-fetch-status.sh <ticket-id> <repo-root>
# Prints status name to stdout. Exit 0 on success; 2 = backend unavailable; 1 = error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

TICKET_ID="${1:-}"
REPO_ROOT="${2:-}"

if [ -z "$TICKET_ID" ] || [ -z "$REPO_ROOT" ]; then
  echo "Usage: ticket-fetch-status.sh <ticket-id> <repo-root>" >&2
  exit 1
fi

REPO_ROOT="$(ticket_repo_root "$REPO_ROOT")" || exit 1
BACKEND=$(ticket_read_backend "$REPO_ROOT")

case "$BACKEND" in
  backlog)
    bash "$SCRIPT_DIR/ticket-fetch-status-backlog.sh" "$TICKET_ID" "$REPO_ROOT"
    ;;
  linear)
    bash "$SCRIPT_DIR/ticket-fetch-status-linear.sh" "$TICKET_ID"
    ;;
  *)
    exit 2
    ;;
esac
