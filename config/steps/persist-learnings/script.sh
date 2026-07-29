set -euo pipefail
: "${ORCHESTRATOR_STEP_DIR:?orchestrator: ORCHESTRATOR_STEP_DIR required}"
exec python3 "${ORCHESTRATOR_STEP_DIR}/persist_learnings.py" "$@"
