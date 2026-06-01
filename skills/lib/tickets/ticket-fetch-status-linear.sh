#!/usr/bin/env bash
# ticket-fetch-status-linear.sh — print Linear issue state name to stdout
# Usage: ticket-fetch-status-linear.sh <ticket-id>
# Requires LINEAR_API_KEY. Exit 2 if key missing; exit 1 on API/parse failure.
set -euo pipefail

TICKET_ID="${1:-}"

if [ -z "$TICKET_ID" ]; then
  echo "Usage: ticket-fetch-status-linear.sh <ticket-id>" >&2
  exit 1
fi

if [ -z "${LINEAR_API_KEY:-}" ]; then
  exit 2
fi

LINEAR_RESPONSE=$(curl --silent --fail \
  -X POST \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"{ issue(id: \\\"$TICKET_ID\\\") { state { name } } }\"}" \
  "https://api.linear.app/graphql" 2>/dev/null) || exit 1

echo "$LINEAR_RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['data']['issue']['state']['name'])
"
