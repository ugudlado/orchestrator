#!/usr/bin/env bash
# ticket-fetch-status-backlog.sh — print Backlog.md task status to stdout
# Usage: ticket-fetch-status-backlog.sh <ticket-id> <repo-root>
# Exit 0 with status name on stdout; exit 2 if CLI missing; exit 1 on lookup/parse failure.
set -euo pipefail

TICKET_ID="${1:-}"
REPO_ROOT="${2:-}"

if [ -z "$TICKET_ID" ] || [ -z "$REPO_ROOT" ]; then
  echo "Usage: ticket-fetch-status-backlog.sh <ticket-id> <repo-root>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"

if ! command -v backlog >/dev/null 2>&1; then
  exit 2
fi

TASK_VIEW=$(cd "$REPO_ROOT" && backlog task view "$TICKET_ID" --plain 2>/dev/null) || exit 1

echo "$TASK_VIEW" | python3 -c "
import re, sys
text = sys.stdin.read()
for pat in (r'(?m)^Status:\s*(.+)\s*$', r'(?m)^status:\s*(.+)\s*$'):
    m = re.search(pat, text)
    if m:
        print(m.group(1).strip())
        sys.exit(0)
sys.exit(1)
"
