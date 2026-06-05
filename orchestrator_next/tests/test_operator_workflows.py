"""Tests for operator workflow step contracts and workflow-report."""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrator_next.operator_workflow import (  # noqa: E402
    load_step_params,
    merge_step_env,
    workflow_step_ids,
)


def test_step_params_from_contract(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    params = load_step_params("ticket-done")
    assert params["TICKET_SYNC_STATUS"] == "Done"
    assert params["TICKET_SYNC_LOG_PREFIX"] == "ticket-done"


def test_merge_step_env_os_environ_overrides_contract(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    monkeypatch.setenv("TICKET_SYNC_STATUS", "Archived")
    merged = merge_step_env("ticket-done", {"REPO_ROOT": "/tmp/repo"})
    assert merged["TICKET_SYNC_STATUS"] == "Archived"
    assert merged["TICKET_SYNC_LOG_PREFIX"] == "ticket-done"


def test_workflow_report_step_contract(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    # workflow-report has no params in contract.yaml (defaults live in the script)
    from pathlib import Path
    contract = Path(_REPO_ROOT) / "config" / "steps" / "workflow-report" / "contract.yaml"
    assert contract.is_file()
    assert "run: script.sh" in contract.read_text()


