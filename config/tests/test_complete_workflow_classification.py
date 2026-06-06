"""T-5 / T-6c: `archive-completed-change` state-mutating classification.

`state_mutating: true` in a step's contract.yaml marks inline steps whose
script moves/deletes state.yaml as a side effect. Such steps must be
pre-recorded into state.yaml BEFORE their script runs, or `record.py` crashes
re-opening the now-moved file (the ORC-66 bug).

`archive-completed-change` is the terminal step for feature/bugfix/autopilot
and moves the active change directory into the archive path.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def test_archive_completed_change_contract_is_state_mutating():
    contract_path = Path(_REPO_ROOT) / "config" / "steps" / "archive-completed-change" / "contract.yaml"
    assert contract_path.is_file(), f"contract.yaml not found at {contract_path}"
    data = yaml.safe_load(contract_path.read_text()) or {}
    assert data.get("state_mutating") is True, (
        f"archive-completed-change/contract.yaml must have `state_mutating: true`; "
        f"got state_mutating={data.get('state_mutating')!r}"
    )
