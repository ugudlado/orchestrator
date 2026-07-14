"""
Tests for dispatch.dispatch() DAG-walk behavior (ORC-63).
"""
from __future__ import annotations

import os
import sys
import textwrap

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.parser import State, StepHistoryEntry  # noqa: E402
from orchestrator_next.dispatch import dispatch  # noqa: E402


# ---------------------------------------------------------------------------
# Contract YAML stubs
# ---------------------------------------------------------------------------

_CONTRACT_EXECUTE_NEXT_TASK = textwrap.dedent("""\
    id: execute-next-task
    agent: developer
    instruction: Execute the next pending task.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
""")

_CONTRACT_RUN_PHASE_REVIEW = textwrap.dedent("""\
    id: run-phase-review
    agent: developer
    instruction: Review the completed phase.
    rules: []
    inputs: []
    outputs: []
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_dir_contract(steps_dir, step_id: str, content: str) -> None:
    """Write a directory-form contract."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(content)
    data = yaml.safe_load(content)
    if data and data.get("agent") and not data.get("run"):
        (step_dir / "prompt.md").write_text(data.get("instruction", "placeholder"))


def _write_contracts(steps_dir) -> None:
    """Write both step contracts to steps_dir."""
    _write_dir_contract(steps_dir, "execute-next-task", _CONTRACT_EXECUTE_NEXT_TASK)
    _write_dir_contract(steps_dir, "run-phase-review", _CONTRACT_RUN_PHASE_REVIEW)


def _write_plan_yaml(state_dir, phase: str = "implement") -> None:
    """Write a plan.yaml with both active steps in the given phase."""
    plan = {
        "feature": "hl-303-repro",
        "schema": "feature",
        "resolved_flags": {},
        "phases": [
            {
                "name": phase,
                "goal": "Execute all pending tasks.",
                "steps": [
                    {
                        "id": "execute-next-task",
                        "agent": "developer",
                        "goal": "Execute the next pending task.",
                        "inputs": [],
                        "outputs": ["task_execution_result"],
                        "rules": [],
                    },
                    {
                        "id": "run-phase-review",
                        "agent": "developer",
                        "goal": "Review the completed phase.",
                        "inputs": [],
                        "outputs": [],
                        "rules": [],
                    },
                ],
            }
        ],
    }
    (state_dir / "plan.yaml").write_text(yaml.safe_dump(plan, sort_keys=False))


def _make_state(tasks_md_path: str, phase: str = "implement") -> State:
    """
    Build a State with execute-next-task already completed in step_history.

    state.raw includes tasks_path (historical field; the tasks_md resolver is gone).
    """
    completed_entry = StepHistoryEntry(
        step_id="execute-next-task",
        phase=phase,
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-05-01T10:00:00Z",
        ended_at="2026-05-01T11:00:00Z",
        usage={"total_tokens": 100},
        escalation=None,
        raw={
            "step_id": "execute-next-task",
            "phase": phase,
            "status": "completed",
            "agent": "developer",
            "attempt": 1,
            "started_at": "2026-05-01T10:00:00Z",
            "ended_at": "2026-05-01T11:00:00Z",
        },
    )
    raw = {
        "change_id": "hl-303-repro",
        "phase": phase,
        "tasks_path": tasks_md_path,  # explicit override — resolver honors this first
    }
    return State(
        change_id="hl-303-repro",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={
            phase: {
                "active": ["execute-next-task", "run-phase-review"],
            }
        },
        step_history=[completed_entry],
        raw=raw,
    )


# ===========================================================================
# ORC-63 T-12: dispatch DAG-walk + node in_progress write + prereq hard block
# ===========================================================================


def _write_nodes_state(state_dir, nodes, phase="main", history=None, extra=None):
    """Write a state.yaml with the ORC-63 nodes-shape workflow_plan."""
    data = {
        "change_id": "orc63-dispatch",
        "phase": phase,
        "repo_root": str(state_dir),
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": history or [],
    }
    if extra:
        data.update(extra)
    p = state_dir / "state.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return str(p)


def _agent_contract(step_id, inputs=None, outputs=None):
    lines = [
        f"id: {step_id}",
        "agent: developer",
        f"instruction: Run {step_id}.",
        "rules: []",
    ]
    if inputs is None:
        lines.append("inputs: []")
    else:
        lines.append("inputs:")
        for i in inputs:
            lines.append(f"  - {i}")
    lines.append(f"outputs: {outputs or []}")
    return "\n".join(lines) + "\n"


def test_dispatch_selects_first_ready_node_no_plan_yaml(tmp_path, monkeypatch):
    """dispatch selects the first ready node from workflow_plan.nodes without
    loading any plan.yaml (none exists)."""
    from orchestrator_next.parser import load_state
    steps = tmp_path / "steps"
    steps.mkdir()
    for sid in ("a", "b"):
        _write_dir_contract(steps, sid, _agent_contract(sid))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    nodes = [
        {"id": "a", "status": "completed", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": []},
        {"id": "b", "status": "pending", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": []},
    ]
    sp = _write_nodes_state(state_dir, nodes)
    state = load_state(sp)
    action, code = dispatch(state, sp)
    assert code == 0
    assert action["step_id"] == "b"
    assert not (state_dir / "plan.yaml").exists()


def test_dispatch_skips_node_with_unmet_depends_on(tmp_path, monkeypatch):
    """A node whose depends_on is unmet is not selected; the first ready one is."""
    from orchestrator_next.parser import load_state
    steps = tmp_path / "steps"
    steps.mkdir()
    for sid in ("a", "b", "c"):
        _write_dir_contract(steps, sid, _agent_contract(sid))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    # a completed; b and c pending. c depends_on b -> only b is ready.
    nodes = [
        {"id": "a", "status": "completed", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": []},
        {"id": "b", "status": "pending", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": [], "depends_on": ["a"]},
        {"id": "c", "status": "pending", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": [], "depends_on": ["b"]},
    ]
    sp = _write_nodes_state(state_dir, nodes)
    state = load_state(sp)
    action, code = dispatch(state, sp)
    assert code == 0
    assert action["step_id"] == "b", "c has an unmet depends_on; b is the ready node"


def test_dispatch_tiebreak_declaration_order(tmp_path, monkeypatch):
    """When several nodes are ready, the first in declaration order wins."""
    from orchestrator_next.parser import load_state
    steps = tmp_path / "steps"
    steps.mkdir()
    for sid in ("a", "b", "c"):
        _write_dir_contract(steps, sid, _agent_contract(sid))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    # b and c both depend only on a (completed) -> both ready; b wins.
    nodes = [
        {"id": "a", "status": "completed", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": []},
        {"id": "b", "status": "pending", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": [], "depends_on": ["a"]},
        {"id": "c", "status": "pending", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": [], "depends_on": ["a"]},
    ]
    sp = _write_nodes_state(state_dir, nodes)
    state = load_state(sp)
    action, code = dispatch(state, sp)
    assert code == 0
    assert action["step_id"] == "b"


def test_dispatch_marks_chosen_node_in_progress(tmp_path, monkeypatch):
    """The chosen node's status becomes in_progress in state.yaml."""
    from orchestrator_next.parser import load_state
    steps = tmp_path / "steps"
    steps.mkdir()
    for sid in ("a", "b"):
        _write_dir_contract(steps, sid, _agent_contract(sid))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    nodes = [
        {"id": "a", "status": "completed", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": []},
        {"id": "b", "status": "pending", "agent": "developer", "goal": "",
         "inputs": [], "outputs": [], "rules": []},
    ]
    sp = _write_nodes_state(state_dir, nodes)
    state = load_state(sp)
    dispatch(state, sp)
    written = yaml.safe_load(open(sp).read())
    by_id = {n["id"]: n for n in written["workflow_plan"]["main"]["nodes"]}
    assert by_id["b"]["status"] == "in_progress"


def test_dispatch_builds_step_context_from_node(tmp_path, monkeypatch):
    """step_context is built from the chosen node dict (not a plan.yaml block)."""
    from orchestrator_next.parser import load_state
    steps = tmp_path / "steps"
    steps.mkdir()
    _write_dir_contract(steps, "a", _agent_contract("a"))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    nodes = [
        {"id": "a", "status": "pending", "agent": "developer", "goal": "Do a.",
         "inputs": [], "outputs": [], "rules": ["rule one"]},
    ]
    sp = _write_nodes_state(state_dir, nodes)
    state = load_state(sp)
    action, code = dispatch(state, sp)
    assert code == 0
    ctx = action["step_context"]
    assert ctx["id"] == "a"
    assert ctx["goal"] == "Do a."




def _write_execute_one_task_contract(contracts_dir) -> None:
    """Directory-form execute-one-task contract (task-node step_contract target)."""
    step_dir = contracts_dir / "execute-one-task"
    step_dir.mkdir(parents=True)
    (step_dir / "contract.yaml").write_text(textwrap.dedent("""\
        id: execute-one-task
        version: 1
        model: auto
        inputs: []
        outputs:
          - task_execution_result
        rules: []
    """))
    (step_dir / "prompt.md").write_text(
        "Implement one task from step_context.task.\n"
    )


def test_dispatch_task_node_resolves_step_contract(tmp_path, monkeypatch):
    """task-T-1 loads execute-one-task via step_contract; must not fall back to inline."""
    from orchestrator_next.parser import load_state

    contracts_dir = tmp_path / "contracts"
    _write_execute_one_task_contract(contracts_dir)
    monkeypatch.setenv(
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
    )

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    nodes = [
        {
            "id": "task-T-1",
            "status": "pending",
            "agent": "developer",
            "step_contract": "execute-one-task",
            "goal": "Wire X to Y",
            "inputs": [],
            "outputs": ["task_execution_result"],
            "rules": [],
            "depends_on": [],
            "task": {
                "id": "T-1",
                "title": "Wire X to Y",
                "files": ["a.py"],
                "verify": ["true"],
                "depends_on": [],
            },
        },
    ]
    sp = _write_nodes_state(state_dir, nodes)
    state = load_state(sp)
    action, code = dispatch(state, sp)
    assert code == 0, f"expected dispatch success, got exit {code}"
    assert action["step_id"] == "task-T-1"
    assert action["model"] == "auto"
    assert action["step_context"]["task"]["id"] == "T-1"


def test_dispatch_resumes_task_node_with_step_contract(tmp_path, monkeypatch):
    """Resume path also resolves step_contract (not last.agent inline placeholder)."""
    from orchestrator_next.parser import load_state

    contracts_dir = tmp_path / "contracts"
    _write_execute_one_task_contract(contracts_dir)
    monkeypatch.setenv(
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
    )

    state_dir = tmp_path / "st"
    state_dir.mkdir()
    nodes = [
        {
            "id": "task-T-1",
            "status": "in_progress",
            "agent": "developer",
            "step_contract": "execute-one-task",
            "goal": "Wire X to Y",
            "inputs": [],
            "outputs": ["task_execution_result"],
            "rules": [],
            "task": {"id": "T-1", "title": "Wire X to Y", "files": ["a.py"], "verify": ["true"]},
        },
    ]
    history = [
        {
            "step_id": "task-T-1",
            "phase": "main",
            "status": "in_progress",
            "agent": "developer",
            "attempt": 1,
            "started_at": "2026-05-25T16:50:36Z",
        },
    ]
    sp = _write_nodes_state(state_dir, nodes, history=history)
    state = load_state(sp)
    action, code = dispatch(state, sp)
    assert code == 0
    assert action.get("is_resume") is True
    assert action["model"] == "auto"
