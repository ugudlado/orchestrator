#!/usr/bin/env bash
# Invokes capture_test_baseline.py (contract.yaml). ORCHESTRATOR_STEP_DIR and workflow env from orchestrator.
set -euo pipefail
: "${ORCHESTRATOR_STEP_DIR:?orchestrator: ORCHESTRATOR_STEP_DIR required}"
exec python3 "${ORCHESTRATOR_STEP_DIR}/capture_test_baseline.py" "$@"
