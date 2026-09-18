"""Judge-assisted triage of abandoned steps (no on_failure edge).

record() asks the TypeSafe judge to classify why a step abandoned. A
`transient` verdict with high confidence and retries remaining resets the
node for another dispatch instead of parking the run on a human. Any other
outcome — low confidence, a non-transient kind, retries exhausted, or the
judge being unavailable — falls back to the existing needs_you behavior
(orchestrator_next/tests/test_abandoned_routing.py covers that path with the
judge off).
"""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next import judge as judge_mod
from orchestrator_next.record import record


def _write_state(tmp_path, nodes, step_history=None, phase="main"):
    state = {
        "change_id": "abandon-judge-test",
        "phase": phase,
        "schema": "feature",
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": step_history or [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


@pytest.fixture(autouse=True)
def isolate_contracts(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))


def _record(state_path, step_id, phase="main", outputs=None):
    return record(
        state_path,
        {
            "step_id": step_id,
            "phase": phase,
            "status": "abandoned",
            "agent": "strong",
            "outputs": outputs or {"reason": "the tool crashed mid-run"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


class _FakeResult:
    def __init__(self, choice, confidence):
        self.choice = choice
        self.confidence = confidence


class _FakeResponse:
    def __init__(self, choices):
        self.choices = choices


def _fake_ask(kind, confidence):
    def _ask(*, state, questions):
        return _FakeResponse({"kind": _FakeResult(kind, confidence)})
    return _ask


def test_transient_high_confidence_resets_node_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_mod, "ask", _fake_ask("transient", 0.9))
    monkeypatch.setattr(judge_mod, "choice", lambda **kw: object())

    path = _write_state(tmp_path, [
        {"id": "design", "status": "in_progress", "depends_on": []},
    ])
    _record(path, "design")
    raw = yaml.safe_load(open(path).read())
    node = raw["workflow_plan"]["main"]["nodes"][0]
    assert node["status"] == "reset"
    assert raw["status"] == "active"
    assert raw["step_history"][-1]["judge"] == {"kind": "transient", "confidence": 0.9}


def test_transient_but_retries_exhausted_falls_back_to_needs_you(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_mod, "ask", _fake_ask("transient", 0.9))
    monkeypatch.setattr(judge_mod, "choice", lambda **kw: object())

    history = [
        {"phase": "main", "step_id": "design", "status": "abandoned"},
        {"phase": "main", "step_id": "design", "status": "abandoned"},
        {"phase": "main", "step_id": "design", "status": "abandoned"},
    ]
    path = _write_state(tmp_path, [
        {"id": "design", "status": "in_progress", "depends_on": [], "max_retries": 3},
    ], step_history=history)
    _record(path, "design")
    raw = yaml.safe_load(open(path).read())
    node = raw["workflow_plan"]["main"]["nodes"][0]
    assert node["status"] == "abandoned"
    assert raw["status"] == "needs_you"


def test_prompt_defect_falls_back_to_needs_you_with_kind_in_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_mod, "ask", _fake_ask("prompt_defect", 0.95))
    monkeypatch.setattr(judge_mod, "choice", lambda **kw: object())

    path = _write_state(tmp_path, [
        {"id": "design", "status": "in_progress", "depends_on": []},
    ])
    _record(path, "design", outputs={"reason": "instructions contradicted each other"})
    raw = yaml.safe_load(open(path).read())
    node = raw["workflow_plan"]["main"]["nodes"][0]
    assert node["status"] == "abandoned"
    assert raw["status"] == "needs_you"
    assert "[prompt_defect]" in raw["needs_you_reason"]


def test_low_confidence_transient_falls_back_to_needs_you(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_mod, "ask", _fake_ask("transient", 0.4))
    monkeypatch.setattr(judge_mod, "choice", lambda **kw: object())

    path = _write_state(tmp_path, [
        {"id": "design", "status": "in_progress", "depends_on": []},
    ])
    _record(path, "design")
    raw = yaml.safe_load(open(path).read())
    node = raw["workflow_plan"]["main"]["nodes"][0]
    assert node["status"] == "abandoned"
    assert raw["status"] == "needs_you"


def test_judge_none_is_unchanged_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_mod, "ask", lambda **kw: None)

    path = _write_state(tmp_path, [
        {"id": "design", "status": "in_progress", "depends_on": []},
    ])
    _record(path, "design", outputs={"reason": "no designable scope"})
    raw = yaml.safe_load(open(path).read())
    node = raw["workflow_plan"]["main"]["nodes"][0]
    assert node["status"] == "abandoned"
    assert raw["status"] == "needs_you"
    assert raw["step_history"][-1]["judge"] is None
    assert "design abandoned: no designable scope" in raw["needs_you_reason"]
