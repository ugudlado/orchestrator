#!/usr/bin/env bash
# ticket-sync-linear.sh — set Linear issue state via HTTPS GraphQL
# Usage: ticket-sync-linear.sh <ticket-id> <target-status> <repo-root>
# Requires LINEAR_API_KEY and linear.team_id in spec/project.yaml.
set -euo pipefail

TICKET_ID="${1:-}"
TARGET_STATUS="${2:-}"
REPO_ROOT="${3:-}"

if [ -z "$TICKET_ID" ] || [ -z "$TARGET_STATUS" ] || [ -z "$REPO_ROOT" ]; then
  echo "Usage: ticket-sync-linear.sh <ticket-id> <target-status> <repo-root>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"

if [ -z "${LINEAR_API_KEY:-}" ]; then
  echo "WARN: LINEAR_API_KEY not set" >&2
  exit 2
fi

export LINEAR_API_KEY
python3 - "$TICKET_ID" "$TARGET_STATUS" "$REPO_ROOT/spec/project.yaml" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
import yaml

ticket_id, target, project_path = sys.argv[1:4]
project = {}
if Path(project_path).is_file():
    with open(project_path) as f:
        project = yaml.safe_load(f) or {}
linear_cfg = project.get("linear") or {}
team_id = linear_cfg.get("team_id")
if not team_id:
    print("WARN: spec/project.yaml missing linear.team_id", file=sys.stderr)
    sys.exit(2)

api_key = os.environ["LINEAR_API_KEY"]
headers = ["-H", f"Authorization: {api_key}", "-H", "Content-Type: application/json"]

def gql(query, variables=None):
    body = {"query": query}
    if variables:
        body["variables"] = variables
    r = subprocess.run(
        ["curl", "--silent", "--fail", "-X", "POST", *headers,
         "-d", json.dumps(body), "https://api.linear.app/graphql"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "curl failed")
    data = json.loads(r.stdout)
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data.get("data") or {}

states = gql(
    "query($teamId: String!) { team(id: $teamId) { states { nodes { id name } } } } }",
    {"teamId": team_id},
)
nodes = states.get("team", {}).get("states", {}).get("nodes") or []
state_id = next((n["id"] for n in nodes if n.get("name") == target), None)
if not state_id:
    print(f"WARN: Linear state {target!r} not found", file=sys.stderr)
    sys.exit(1)

issue = gql("query($id: String!) { issue(id: $id) { id } }", {"id": ticket_id})
issue_uuid = issue.get("issue", {}).get("id")
if not issue_uuid:
    print(f"WARN: Linear issue not found: {ticket_id}", file=sys.stderr)
    sys.exit(1)

gql(
    "mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: { stateId: $stateId }) { success } }",
    {"id": issue_uuid, "stateId": state_id},
)
print(f"ticket-sync-linear: {ticket_id} -> {target}", file=sys.stderr)
PY
