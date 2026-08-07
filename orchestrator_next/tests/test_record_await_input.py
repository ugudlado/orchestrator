"""record() accepts status await_input without advancing the DAG."""
from __future__ import annotations

import yaml

from orchestrator_next.record import record
from orchestrator_next import readiness
from orchestrator_next.parser import load_state


def _state(tmp_path, *, nodes=None) -> str:
    nodes = nodes or [
        {"id": "intake", "status": "in_progress"},
        {"id": "synthesize", "status": "pending", "depends_on": ["intake"]},
    ]
    state = {
        "change_id": "sess-1",
        "phase": "main",
        "status": "active",
        "workflow_plan": {"main": {"nodes": nodes, "filtered": []}},
        "next_step": {"phase": "main", "step_id": "intake"},
        "step_history": [
            {
                "step_id": "intake",
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


def test_await_input_accepted_and_does_not_advance(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {
            "ask": "Who is the audience?",
            "missing": ["audience"],
        },
    })
    assert code == 0, result
    assert result["next_step"] == {"phase": "main", "step_id": "intake"}

    raw = yaml.safe_load(open(path))
    assert raw["next_step"] == {"phase": "main", "step_id": "intake"}
    last = raw["step_history"][-1]
    assert last["status"] == "await_input"
    assert last["outputs"]["ask"] == "Who is the audience?"
    assert last["outputs"]["missing"] == ["audience"]

    by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    assert by_id["intake"]["status"] == "in_progress"
    assert by_id["synthesize"]["status"] == "pending"

    state = load_state(path)
    assert readiness.next_ready_node(state) == "intake"
    assert readiness.is_node_ready(state, "intake")
    assert not readiness.is_node_ready(state, "synthesize")


def test_await_input_no_retry_cap(tmp_path, monkeypatch):
    """Repeated await_input must not bump retries or block."""
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path, nodes=[
        {
            "id": "intake",
            "status": "in_progress",
            "on_failure": "intake",
            "max_retries": 1,
        },
    ])
    for i in range(3):
        result, code = record(path, {
            "step_id": "intake",
            "phase": "main",
            "status": "await_input",
            "outputs": {"ask": f"round {i}"},
        })
        assert code == 0, result
        raw = yaml.safe_load(open(path))
        assert raw.get("status") != "blocked"
        assert raw.get("retries", {}).get("intake", 0) == 0
        assert raw["next_step"]["step_id"] == "intake"


def test_await_input_does_not_require_reason(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {"ask": "Need more detail"},
    })
    assert code == 0, result


def test_await_input_persists_awaiting_block_with_options(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {
            "ask": "Review passed. Ship it, or send back?",
            "options": [
                {"label": "approve"},
                {"label": "rework implementation", "reset_to": "intake"},
            ],
        },
    })
    assert code == 0, result

    raw = yaml.safe_load(open(path))
    assert raw["awaiting"] == {
        "step_id": "intake",
        "ask": "Review passed. Ship it, or send back?",
        "options": [
            {"label": "approve"},
            {"label": "rework implementation", "reset_to": "intake"},
        ],
    }


def test_await_input_option_missing_label_rejected(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {"ask": "?", "options": [{"reset_to": "intake"}]},
    })
    assert code == 3
    assert result["reason"] == "invalid_options"


def test_await_input_option_reset_to_unknown_node_rejected(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {"ask": "?", "options": [{"label": "x", "reset_to": "does-not-exist"}]},
    })
    assert code == 3
    assert result["reason"] == "invalid_options"


def test_await_input_option_reset_to_after_current_step_rejected(tmp_path, monkeypatch):
    """intake is before synthesize in the DAG — an option pointing forward is invalid."""
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {"ask": "?", "options": [{"label": "x", "reset_to": "synthesize"}]},
    })
    assert code == 3
    assert result["reason"] == "invalid_options"


def test_await_input_options_empty_list_rejected(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {"ask": "?", "options": []},
    })
    assert code == 3
    assert result["reason"] == "invalid_options"


def test_awaiting_block_cleared_on_completed(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    record(path, {
        "step_id": "intake", "phase": "main", "status": "await_input",
        "outputs": {"ask": "?", "options": [{"label": "approve"}]},
    })
    raw = yaml.safe_load(open(path))
    assert "awaiting" in raw

    record(path, {
        "step_id": "intake", "phase": "main", "status": "completed",
        "outputs": {"reason": "done"},
    })
    raw = yaml.safe_load(open(path))
    assert "awaiting" not in raw


def test_completed_after_await_input_advances(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    path = _state(tmp_path)
    record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "await_input",
        "outputs": {"ask": "Who?"},
    })
    result, code = record(path, {
        "step_id": "intake",
        "phase": "main",
        "status": "completed",
        "outputs": {"reason": "checklist complete"},
    })
    assert code == 0, result
    raw = yaml.safe_load(open(path))
    by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    assert by_id["intake"]["status"] == "completed"
    assert raw["next_step"]["step_id"] == "synthesize"
