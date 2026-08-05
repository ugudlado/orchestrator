#!/usr/bin/env bash
# intake-research: normalize the <id> topic into spec/changes/<slug>/topic.md.
# Reads $CHANGE_ID (the free-form topic) and writes topic context under
# ${ORCHESTRATOR_WORKFLOW_DIR:-$REPO_ROOT}/spec/changes/<slug>/.
set -euo pipefail

: "${CHANGE_ID:?orchestrator: CHANGE_ID required}"

BASE="${ORCHESTRATOR_WORKFLOW_DIR:-${REPO_ROOT:?orchestrator: ORCHESTRATOR_WORKFLOW_DIR or REPO_ROOT required}}"
SLUG="$(printf '%s' "${CHANGE_ID}" | tr -s ' ' | tr ' ' '-' | tr -cd '[:alnum:]_-' | tr '[:upper:]' '[:lower:]' | sed 's/^-//;s/-$//')"
CHANGE_DIR="${BASE}/spec/changes/${SLUG}"

mkdir -p "${CHANGE_DIR}"
TOPIC="$(printf '%s' "${CHANGE_ID}" | tr -s ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

cat > "${CHANGE_DIR}/topic.md" <<EOF
# Research Topic

**Topic:** ${TOPIC}

**Slug:** ${SLUG}
**Intake time:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

echo "{\"status\":\"completed\",\"outputs\":{\"topic\":\"${TOPIC}\",\"topic_file\":\"${CHANGE_DIR}/topic.md\",\"slug\":\"${SLUG}\"}}"
