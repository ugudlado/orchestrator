"""Tests for orchestrator_next.parser — phase_nodes read path and optional input parsing."""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def steps_dir(tmp_path):
    """Create a temp steps directory and set env override."""
    d = tmp_path / "steps"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def set_override(steps_dir, monkeypatch):
    """Point load_contract_for_step to the temp steps dir."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_contract(steps_dir, step_id: str, data: dict):
    """Write a directory-form contract (canonical form since flat-file removed)."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(data))
    if data.get("agent") and not data.get("run"):
        (step_dir / "prompt.md").write_text(data.get("instruction", "placeholder"))


# ---------------------------------------------------------------------------
# ORC-63 T-1: parser.phase_nodes node-shape read path (AC-1, AC-11)
# ---------------------------------------------------------------------------


def test_fail_on_await_input_is_reserved(tmp_path):
    from orchestrator_next.parser import ContractError, load_contract_for_step

    root = tmp_path / "pack"
    _write_contract(
        root / "steps",
        "judge",
        {
            "id": "judge",
            "kind": "judgment",
            "prompt": "SKILL.md",
            "out": {"verdict": {"type": "enum", "values": ["pass", "await_input"], "fail_on": ["await_input"]}},
        },
    )
    (root / "steps" / "judge" / "SKILL.md").write_text("# judge\n")
    with pytest.raises(ContractError, match="await_input is reserved"):
        load_contract_for_step("judge", root)
