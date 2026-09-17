"""`orchestrator step` reports a status, not an exit code (protocol v2 §10).

The v1 dispatch protocol said "exit 0 + JSON with a model key", "exit 1
complete", "exit 2 blocked". v2 collapses all three into one JSON result whose
`status` says which it is. These are the same three scenarios, re-expressed.
"""
from __future__ import annotations

import textwrap

import yaml

from orchestrator_next.protocol import step
from orchestrator_next.tests.conftest import install_step_models


def _state(tmp_path, change_id: str, step_history: list) -> str:
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "feature",
        "change_id": change_id,
        "phase": "implement",
        "repo_root": str(tmp_path),
        "step_history": step_history,
        "workflow_plan": {"implement": {
            "nodes": [{"id": "my-step", "status": "pending"}],
            "filtered": [],
        }},
    }, sort_keys=False), encoding="utf-8")
    return str(path)


def _judgment_contract(tmp_path, monkeypatch) -> None:
    steps_dir = tmp_path / "steps"
    d = steps_dir / "my-step"
    d.mkdir(parents=True)
    (d / "contract.yaml").write_text(textwrap.dedent("""\
        id: my-step
        version: 1
        kind: judgment
        prompt: prompt.md
        out:
          note: {type: string}
    """), encoding="utf-8")
    (d / "prompt.md").write_text("Do something.\n", encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    install_step_models(monkeypatch, tmp_path, ("my-step",), alias="auto")


def test_pending_judgment_step_is_ready_with_a_payload(tmp_path, monkeypatch):
    """The v1 'exit 0 + JSON with a model key' case."""
    _judgment_contract(tmp_path, monkeypatch)
    result, code = step(_state(tmp_path, "t-ready", []))

    assert code == 0
    assert result["status"] == "ready"
    assert result["kind"] == "judgment"
    assert result["step_id"] == "my-step"
    assert result["payload"]["model"] == "auto"
    # v1's `action` envelope is gone: the harness reads `payload`.
    assert "action" not in result


def test_all_steps_done_reports_status_done(tmp_path, monkeypatch):
    """The v1 'exit 1, no JSON' case."""
    _judgment_contract(tmp_path, monkeypatch)
    history = [{"step_id": "my-step", "phase": "implement",
                "status": "completed", "agent": "developer", "attempt": 1}]
    result, code = step(_state(tmp_path, "t-done", history))

    assert code == 0
    assert result["status"] == "done"
    assert result["step_id"] is None


def test_blocked_step_reports_status_blocked(tmp_path, monkeypatch):
    """The v1 'exit 2, no JSON' case."""
    _judgment_contract(tmp_path, monkeypatch)
    history = [{"step_id": "my-step", "phase": "implement",
                "status": "blocked", "agent": "developer", "attempt": 1}]
    result, code = step(_state(tmp_path, "t-blocked", history))

    assert code == 0
    assert result["status"] == "blocked"
    assert result["detail"]
