#!/usr/bin/env bash
# Invokes workflow_report_step.py (contract.yaml). ORCHESTRATOR_STEP_DIR and workflow env from orchestrator.
set -euo pipefail
: "${ORCHESTRATOR_STEP_DIR:?orchestrator: ORCHESTRATOR_STEP_DIR required}"
exec "${ORCHESTRATOR_PYTHON:-python3}" "${ORCHESTRATOR_STEP_DIR}/workflow_report_step.py" "$@"
