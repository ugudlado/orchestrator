"""record() outputs.reset_to resets the DAG from a chosen earlier step."""
from __future__ import annotations

import yaml

from orchestrator_next.record import record
from orchestrator_next import readiness
from orchestrator_next.parser import load_state


def _state(tmp_path, *, nodes=None) -> str:
    nodes = nodes or [
        {"id": "design", "status": "completed"},
        {"id": "implement", "status": "completed"},
        {"id": "code-review", "status": "completed"},
        {
            "id": "human-review",
            "status": "in_progress",
            "on_failure": "implement",
            "max_retries": 8,
        },
        {"id": "ship", "status": "pending", "depends_on": ["human-review"]},
    ]
    state = {
        "change_id": "feat-1",
        "phase": "main",
        "status": "active",
        "workflow_plan": {"main": {"nodes": nodes, "filtered": []}},
        "next_step": {"phase": "main", "step_id": "human-review"},
        "step_history": [
            {
                "step_id": "human-review",
                "phase": "main",
                "status": "in_progress",
                "attempt": 1,
            }
        ],
        "retries": {},
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def test_reset_to_design_resets_forward_nodes(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "human-review",
        "phase": "main",
        "status": "failed",
        "outputs": {
            "reset_to": "design",
            "reason": "AC wrong",
        },
    })
    assert code == 0, result
    raw = yaml.safe_load(open(path))
    by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    assert by_id["design"]["status"] == "pending"
    assert by_id["implement"]["status"] == "pending"
    assert by_id["code-review"]["status"] == "pending"
    assert by_id["human-review"]["status"] in ("pending", "reset")
    assert by_id["ship"]["status"] == "pending"
    # Failed gate entry retained for audit
    assert any(
        e.get("step_id") == "human-review" and e.get("status") == "failed"
        for e in raw["step_history"]
        if isinstance(e, dict)
    )
    state = load_state(path)
    assert readiness.next_ready_node(state) == "design"
    assert result["next_step"]["step_id"] == "design"


def test_reset_to_implement(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "human-review",
        "phase": "main",
        "status": "failed",
        "outputs": {"reset_to": "implement", "reason": "add task"},
    })
    assert code == 0, result
    raw = yaml.safe_load(open(path))
    by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    assert by_id["design"]["status"] == "completed"  # before implement — untouched
    assert by_id["implement"]["status"] == "pending"
    assert readiness.next_ready_node(load_state(path)) == "implement"


def test_failed_without_reset_to_uses_on_failure(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "human-review",
        "phase": "main",
        "status": "failed",
        "outputs": {"reason": "fallback"},
    })
    assert code == 0, result
    raw = yaml.safe_load(open(path))
    by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    # Static on_failure: only gate + implement reset (not full DAG)
    assert by_id["implement"]["status"] in ("pending", "reset")
    assert by_id["human-review"]["status"] in ("pending", "reset")
    assert by_id["design"]["status"] == "completed"


def test_invalid_reset_to_falls_back(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "human-review",
        "phase": "main",
        "status": "failed",
        "outputs": {"reset_to": "not-a-step", "reason": "invalid target"},
    })
    assert code == 0, result
    raw = yaml.safe_load(open(path))
    by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    assert by_id["design"]["status"] == "completed"
    assert by_id["implement"]["status"] in ("pending", "reset")
