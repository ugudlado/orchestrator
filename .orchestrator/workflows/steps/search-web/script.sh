#!/usr/bin/env bash
# search-web: query Tavily for the topic, write spec/changes/<slug>/sources.json.
# Reads the slug from CHANGE_ID; uses TAVILY_API_KEY.
set -euo pipefail

: "${CHANGE_ID:?orchestrator: CHANGE_ID required}"

BASE="${ORCHESTRATOR_WORKFLOW_DIR:-${REPO_ROOT:?orchestrator: ORCHESTRATOR_WORKFLOW_DIR or REPO_ROOT required}}"
SLUG="$(printf '%s' "${CHANGE_ID}" | tr -s ' ' | tr ' ' '-' | tr -cd '[:alnum:]_-' | tr '[:upper:]' '[:lower:]' | sed 's/^-//;s/-$//')"
CHANGE_DIR="${BASE}/spec/changes/${SLUG}"

TOPIC="$(grep -m1 '^\*\*Topic:\*\*' "${CHANGE_DIR}/topic.md" | sed 's/^\*\*Topic:\*\*[[:space:]]*//')"

# Python helper for the Tavily call (avoids bash+curl JSON parsing fragility).
export TOPIC
exec python3 - "${CHANGE_DIR}" <<'PYEOF'
import json
import os
import sys
import urllib.request

change_dir = sys.argv[1]
topic = os.environ.get("TOPIC", "").strip()
key = os.environ.get("TAVILY_API_KEY", "").strip()

results = []
if not key:
    results = [{"title": "(no TAVILY_API_KEY set)", "url": "", "content": ""}]
else:
    try:
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps({"api_key": key, "query": topic, "max_results": 4,
                             "search_depth": "advanced"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        results = body.get("results", [])
    except Exception as exc:  # noqa: BLE001
        results = [{"title": f"(search error: {exc})", "url": "", "content": ""}]

payload = {"topic": topic, "results": results}
with open(os.path.join(change_dir, "sources.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(json.dumps({"status": "completed", "outputs": {"count": len(results),
                                                      "sources_file": os.path.join(change_dir, "sources.json")}}))
PYEOF
