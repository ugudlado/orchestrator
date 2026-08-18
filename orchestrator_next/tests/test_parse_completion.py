"""parse_completion must accept every status record.py implements, including
await_input — the human-review charter instructs agents to emit it, but
VALID_STATUSES omitted it, so a compliant COMPLETION raised ValueError in
run_agent_step and got recorded as a retryable failed instead of routing to
the awaiting state.
"""
from __future__ import annotations

from orchestrator_next.parse_completion import parse_completion


def test_await_input_status_parses():
    text = """
COMPLETION:
  step_id: human-review
  phase: main
  status: await_input
  outputs:
    ask: "Approve this design?"
    options:
      - label: "yes"
      - label: "no"
"""
    completion = parse_completion(text)
    assert completion["status"] == "await_input"
    assert completion["outputs"]["ask"] == "Approve this design?"


def test_await_input_status_records_awaiting_and_pins_next_step(tmp_path, monkeypatch):
    """End-to-end: a parsed await_input COMPLETION recorded via record() sets
    state_raw["awaiting"] and keeps next_step pinned to the same step."""
    import textwrap

    import yaml

    from orchestrator_next import record
    from orchestrator_next.tests.conftest import install_step_models

    contracts_dir = tmp_path / "contracts"
    step_dir = contracts_dir / "human-review"
    step_dir.mkdir(parents=True)
    (step_dir / "contract.yaml").write_text(textwrap.dedent("""\
        id: human-review
        version: 1
        kind: agent
        agent: architect
        inputs: []
        outputs: []
        rules: []
    """))
    (step_dir / "prompt.md").write_text("Ask for approval.\n")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))
    install_step_models(monkeypatch, tmp_path, ["human-review"])

    state = {
        "change_id": "test-1",
        "phase": "main",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {
            "main": {
                "nodes": [{"id": "human-review", "status": "pending", "agent": "architect"}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))

    text = """
COMPLETION:
  step_id: human-review
  phase: main
  status: await_input
  outputs:
    ask: "Approve this design?"
"""
    completion = parse_completion(text)
    payload = {
        "step_id": completion["step_id"],
        "phase": completion["phase"],
        "status": completion["status"],
        "agent": "architect",
        "outputs": completion["outputs"],
    }
    result, code = record.record(str(state_path), payload)
    assert code == 0, result

    raw = yaml.safe_load(state_path.read_text())
    assert raw["awaiting"]["step_id"] == "human-review"
    assert raw["awaiting"]["ask"] == "Approve this design?"
    assert raw["next_step"] == {"phase": "main", "step_id": "human-review"}
