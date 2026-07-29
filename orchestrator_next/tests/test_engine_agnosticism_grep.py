"""ORC-117 T-6: AC-6 grep guard — engine contains no references to specific review step_ids or output keys."""
from __future__ import annotations

import glob
import os
import subprocess

_ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_no_specific_step_ids_in_engine():
    """grep for specific step/output identifiers returns no matches in engine (non-test) .py files."""
    py_files = [
        f for f in glob.glob(os.path.join(_ENGINE_DIR, "*.py"))
        if "tests" not in f
    ]
    if not py_files:
        return  # nothing to check
    result = subprocess.run(
        ["grep", "-lE", "design-review|run-phase-review|design_review_result|phase_review_report"]
        + py_files,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and result.stdout.strip() == "", (
        f"Found specific step/output references in engine files:\n{result.stdout}"
    )


def test_no_optional_history_keys_constant():
    """_OPTIONAL_STEP_HISTORY_KEYS constant no longer exists in record.py."""
    from orchestrator_next import record
    assert not hasattr(record, "_OPTIONAL_STEP_HISTORY_KEYS")
