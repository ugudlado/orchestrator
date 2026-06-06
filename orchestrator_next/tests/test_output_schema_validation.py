"""Tests for contract output_schema → Pydantic validation at the record boundary."""
from __future__ import annotations

import yaml
import pytest
from pathlib import Path

from orchestrator_next.record import record


def _state(tmp_path, step_id: str = "analyze") -> str:
    state = {
        "change_id": "schema-test",
        "phase": "main",
        "workflow_plan": {
            "main": {
                "nodes": [{"id": step_id, "status": "in_progress"}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_contract(contracts_dir: Path, step_id: str, output_schema: dict) -> None:
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    contract = {"id": step_id, "model": "sonnet", "output_schema": output_schema}
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(contract, sort_keys=False))
    (step_dir / "prompt.md").write_text("Analyze.")


def _payload(step_id: str, outputs: dict) -> dict:
    return {
        "step_id": step_id,
        "phase": "main",
        "status": "completed",
        "agent": "analyst",
        "outputs": outputs,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


class TestOutputSchemaValidation:

    @pytest.fixture(autouse=True)
    def contracts(self, tmp_path, monkeypatch):
        self.contracts_dir = tmp_path / "steps"
        self.contracts_dir.mkdir()
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(self.contracts_dir)
        )

    def test_valid_outputs_pass(self, tmp_path):
        _write_contract(self.contracts_dir, "analyze", {
            "verdict": {"type": "str", "required": True},
            "score": {"type": "int"},
        })
        state_path = _state(tmp_path)
        result, code = record(state_path, _payload("analyze", {"verdict": "pass", "score": 9}))
        assert code == 0, result

    def test_missing_required_field_fails(self, tmp_path):
        _write_contract(self.contracts_dir, "analyze", {
            "verdict": {"type": "str", "required": True},
        })
        state_path = _state(tmp_path)
        result, code = record(state_path, _payload("analyze", {}))
        assert code == 3
        assert result["reason"] == "output_schema_validation_failed"
        assert any(e["field"] == "verdict" for e in result["errors"])

    def test_optional_field_may_be_absent(self, tmp_path):
        _write_contract(self.contracts_dir, "analyze", {
            "verdict": {"type": "str", "required": True},
            "score": {"type": "int"},
        })
        state_path = _state(tmp_path)
        result, code = record(state_path, _payload("analyze", {"verdict": "pass"}))
        assert code == 0, result

    def test_no_output_schema_skips_validation(self, tmp_path):
        """Contract without output_schema never rejects outputs."""
        step_dir = self.contracts_dir / "bare"
        step_dir.mkdir()
        (step_dir / "contract.yaml").write_text(yaml.safe_dump({"id": "bare", "model": "sonnet"}))
        (step_dir / "prompt.md").write_text("Bare step.")
        state_path = _state(tmp_path, step_id="bare")
        result, code = record(state_path, _payload("bare", {}))
        assert code == 0, result

    def test_state_yaml_unchanged_on_schema_failure(self, tmp_path):
        _write_contract(self.contracts_dir, "analyze", {
            "verdict": {"type": "str", "required": True},
        })
        state_path = _state(tmp_path)
        pre = Path(state_path).read_bytes()
        record(state_path, _payload("analyze", {}))
        assert Path(state_path).read_bytes() == pre
