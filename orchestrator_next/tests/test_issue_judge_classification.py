"""Judge-assisted classification of workflow_issues (record._classify_issues).

Issues arriving via payload.workflow_issues without a `kind` get one batched
judge call (one Choice question per issue index). judge None leaves issues
exactly as accumulated today.
"""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next import judge as judge_mod
from orchestrator_next.record import record


def _minimal_state(tmp_path) -> str:
    state = {
        "change_id": "issue-judge-test",
        "phase": "implement",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "schema": "feature",
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {"id": "explore", "status": "in_progress", "agent": "discoverer",
                     "goal": "Explore", "inputs": [], "outputs": [], "rules": []},
                ],
                "filtered": [],
            }
        },
        "step_history": [
            {"step_id": "explore", "phase": "implement", "status": "in_progress",
             "evidence": {"outputs": {"reason": "test"}}},
        ],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


@pytest.fixture(autouse=True)
def isolate_contracts(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))


def _payload(issues):
    return {
        "step_id": "explore",
        "phase": "implement",
        "status": "completed",
        "agent": "discoverer",
        "outputs": {"reason": "test"},
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "workflow_issues": issues,
    }


class _FakeResult:
    def __init__(self, choice):
        self.choice = choice
        self.confidence = 0.9


class _FakeResponse:
    def __init__(self, choices):
        self.choices = choices


def test_issue_kinds_filled_by_judge(tmp_path, monkeypatch):
    def _ask(*, state, questions):
        assert set(questions.keys()) == {"0", "1"}
        return _FakeResponse({
            "0": _FakeResult("script_failed"),
            "1": _FakeResult("prompt_gap"),
        })

    monkeypatch.setattr(judge_mod, "ask", _ask)
    monkeypatch.setattr(judge_mod, "choice", lambda **kw: object())

    state_path = _minimal_state(tmp_path)
    record(state_path, _payload([
        {"summary": "tool crashed then worked"},
        {"summary": "missing instructions"},
    ]))
    on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
    kinds = [i["kind"] for i in on_disk["workflow_issues"]]
    assert kinds == ["script_failed", "prompt_gap"]


def test_issue_with_existing_kind_is_not_reclassified(tmp_path, monkeypatch):
    calls = []

    def _ask(*, state, questions):
        calls.append(questions)
        return _FakeResponse({"0": _FakeResult("other")})

    monkeypatch.setattr(judge_mod, "ask", _ask)
    monkeypatch.setattr(judge_mod, "choice", lambda **kw: object())

    state_path = _minimal_state(tmp_path)
    record(state_path, _payload([
        {"summary": "already classified", "kind": "retry_success"},
    ]))
    on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
    assert on_disk["workflow_issues"][0]["kind"] == "retry_success"
    assert calls == []  # judge never asked — nothing needed classification


def test_judge_none_leaves_issues_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_mod, "ask", lambda **kw: None)

    state_path = _minimal_state(tmp_path)
    record(state_path, _payload([{"summary": "some issue"}]))
    on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
    assert "kind" not in on_disk["workflow_issues"][0]
