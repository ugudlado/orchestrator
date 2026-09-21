"""
ORC-48 regression tests: agent + agent_id fields in done payload.
"""
from __future__ import annotations

import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)



def _write_state(tmp_path, *, repo_root: str = "/tmp") -> str:
    """Write a minimal valid state.yaml and return its path string."""
    state = {
        "schema": "bugfix",
        "change_id": "orc-48-test",
        "repo_root": repo_root,
        "phase": "main",
        "workflow_plan": {
            "main": {
                "active": ["diagnose"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_contract(contracts_dir, step_id: str, *, agent: bool = True) -> None:
    """Write a minimal step contract (directory form)."""
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    if agent:
        data = {"id": step_id, "version": 1, "prompt": "prompt.md"}
        (step_dir / "prompt.md").write_text("test instruction")
    else:
        data = {"id": step_id, "version": 1, "run": "script.sh"}
        (step_dir / "script.sh").write_text("#!/bin/sh\nexit 0\n")
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(data))
