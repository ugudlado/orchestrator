"""orc-85 RED: spawn-failure cap (AC-1, AC-3) and completion stickiness (AC-2, AC-4).

Tests fail until T-2 adds `_consecutive_spawn_failures` / `_max_spawn_failures` in
dispatch.py and makes `_effective_node_status` history-authoritative for promoted plans.
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

from orchestrator_next import readiness  # noqa: E402
from orchestrator_next.dispatch import dispatch  # noqa: E402
from orchestrator_next.parser import load_state  # noqa: E402


# Every node id used across these tests gets its own directory-form contract.
_TASK_STEP_IDS = (
    "task-T-1", "task-T-2", "task-A", "task-B", "my-step", "next-step",
)


def _write_execute_one_task_contract(contracts_dir) -> None:
    for step_id in _TASK_STEP_IDS:
        step_dir = contracts_dir / step_id
        step_dir.mkdir(parents=True)
        (step_dir / "contract.yaml").write_text(textwrap.dedent(f"""\
            id: {step_id}
            version: 1
            kind: agent
            agent: developer
            inputs: []
            outputs:
              - task_execution_result
            rules: []
        """))
        (step_dir / "prompt.md").write_text("Implement one task from step_context.task.\n")


def _setup(
    tmp_path,
    monkeypatch,
    state: dict,
    *,
    max_spawn_failures: int = 3,
) -> str:
    """Write project.yaml, contracts override, state.yaml; return state path."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    _write_execute_one_task_contract(contracts_dir)
    monkeypatch.setenv(
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
    )
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "quality_bar": {
                    "max_spawn_failures": max_spawn_failures,
                    "max_retry_rounds": 8,
                }
            }
        )
    )
    state.setdefault("repo_root", str(tmp_path))
    state.setdefault("worktree_path", str(tmp_path))
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _spawn_failure_entry(
    step_id: str,
    phase: str,
    attempt: int,
) -> dict:
    return {
        "step_id": step_id,
        "phase": phase,
        "status": "failed",
        "agent": "developer",
        "attempt": attempt,
        "started_at": f"2026-05-25T21:00:{attempt:02d}Z",
        "ended_at": f"2026-05-25T21:00:{attempt:02d}Z",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "model": "none",
            "cost_usd": 0.0,
        },
        "evidence": {
            "outputs": {
                "task_execution_result": {"status": "failed", "exit_code": 1},
            },
        },
    }


def _agent_failure_entry(
    step_id: str,
    phase: str,
    attempt: int,
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    model: str = "claude-opus-4-7",
) -> dict:
    return {
        "step_id": step_id,
        "phase": phase,
        "status": "failed",
        "agent": "developer",
        "attempt": attempt,
        "started_at": f"2026-05-25T21:00:{attempt:02d}Z",
        "ended_at": f"2026-05-25T21:00:{attempt:02d}Z",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": model,
            "cost_usd": 0.01,
        },
    }


def _terminal_history_entry(
    step_id: str,
    phase: str,
    status: str,
    attempt: int,
) -> dict:
    return {
        "step_id": step_id,
        "phase": phase,
        "status": status,
        "agent": "developer",
        "attempt": attempt,
        "started_at": f"2026-05-25T21:47:{attempt:02d}Z",
        "outputs": {
            "task_execution_result": {
                "task_id": "T-1",
                "status": status,
                "exit_code": 0,
            },
        },
    }


def _task_node(
    node_id: str,
    *,
    status: str = "pending",
    depends_on: list[str] | None = None,
) -> dict:
    node = {
        "id": node_id,
        "status": status,
        "agent": "developer",
        "goal": f"Run {node_id}",
        "inputs": [],
        "outputs": ["task_execution_result"],
        "rules": [],
        "depends_on": depends_on or [],
        "task": {
            "id": node_id.replace("task-", ""),
            "title": node_id,
            "files": [],
            "verify": ["true"],
            "depends_on": [],
        },
    }
    return node


def _promoted_plan_state(
    tmp_path,
    *,
    phase: str = "main",
    nodes: list[dict],
    step_history: list[dict],
) -> dict:
    return {
        "change_id": "orc-85-fixture",
        "phase": phase,
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": step_history,
    }



def _orc84_storm_completion_storm_history() -> list[dict]:
    """Subset of orc-84 task-T-1: spawn storm, completed attempt 11, more spawn failures."""
    history: list[dict] = []
    for attempt in range(1, 11):
        history.append(_spawn_failure_entry("task-T-1", "main", attempt))
    history.append(_terminal_history_entry("task-T-1", "main", "completed", 11))
    for attempt in range(12, 15):
        history.append(_spawn_failure_entry("task-T-1", "main", attempt))
    return history


# ---------------------------------------------------------------------------
# AC-1: spawn-failure cap
# ---------------------------------------------------------------------------


def test_three_consecutive_model_none_failures_for_same_step_id_returns_exit_2_with_spawn_failure_cap_reason(
    tmp_path, monkeypatch, capsys
):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[_task_node("task-T-1", status="pending")],
        step_history=[
            _spawn_failure_entry("task-T-1", "main", 1),
            _spawn_failure_entry("task-T-1", "main", 2),
            _spawn_failure_entry("task-T-1", "main", 3),
        ],
    )
    state_path = _setup(tmp_path, monkeypatch, state)
    action, code = dispatch(load_state(state_path), state_path)

    assert code == 2, f"expected exit 2 (spawn_failure_cap), got {code}"
    assert action.get("reason") == "spawn_failure_cap", (
        f"expected blocked reason spawn_failure_cap, got {action!r}"
    )
    err = capsys.readouterr().err
    assert "spawn_failure_cap" in err
    assert "task-T-1" in err


def test_two_consecutive_model_none_failures_then_a_third_for_a_different_step_id_does_not_trip_cap(
    tmp_path, monkeypatch,
):
    """Spawn-failure counter is per (phase, step_id); two on A + one on B must not cap B."""
    state = _promoted_plan_state(
        tmp_path,
        nodes=[
            _task_node("task-A", status="completed"),
            _task_node("task-B", status="pending", depends_on=["task-A"]),
        ],
        step_history=[
            _spawn_failure_entry("task-A", "main", 1),
            _spawn_failure_entry("task-A", "main", 2),
            _spawn_failure_entry("task-B", "main", 1),
        ],
    )
    state_path = _setup(tmp_path, monkeypatch, state)
    action, code = dispatch(load_state(state_path), state_path)

    assert code == 0, f"expected dispatch to proceed for task-B, got exit {code}"
    assert action.get("step_id") == "task-B"
    assert action.get("reason") != "spawn_failure_cap"


# ---------------------------------------------------------------------------
# AC-2: completion stickiness via step_history
# ---------------------------------------------------------------------------


def test_completed_entry_in_step_history_makes_next_ready_node_skip_that_node_on_promoted_nodes_plan(
    tmp_path, monkeypatch,
):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[
            _task_node("task-T-1", status="pending"),
            _task_node("task-T-2", status="pending", depends_on=["task-T-1"]),
        ],
        step_history=[_terminal_history_entry("task-T-1", "main", "completed", 1)],
    )
    state_path = _setup(tmp_path, monkeypatch, state)
    state = load_state(state_path)

    assert readiness.next_ready_node(state) == "task-T-2"
    assert readiness.is_node_ready(state, "task-T-1") is False


def test_completed_entry_in_step_history_makes_next_ready_node_skip_that_node_on_sequential_nodes_plan(
    tmp_path, monkeypatch,
):
    """History-authoritative completion: completed step_history entry overrides in_progress node."""
    state = _promoted_plan_state(
        tmp_path,
        nodes=[
            _task_node("my-step", status="in_progress"),
            _task_node("next-step", status="pending", depends_on=["my-step"]),
        ],
        step_history=[_terminal_history_entry("my-step", "main", "completed", 1)],
    )
    state_path = _setup(tmp_path, monkeypatch, state)
    state = load_state(state_path)

    assert readiness.is_node_ready(state, "my-step") is False
    assert readiness.next_ready_node(state) == "next-step"


def test_recovered_entry_in_step_history_also_terminates_node(tmp_path, monkeypatch):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[
            _task_node("task-T-1", status="in_progress"),
            _task_node("task-T-2", status="pending", depends_on=["task-T-1"]),
        ],
        step_history=[_terminal_history_entry("task-T-1", "main", "recovered", 1)],
    )
    state_path = _setup(tmp_path, monkeypatch, state)
    state = load_state(state_path)

    assert readiness.is_node_ready(state, "task-T-1") is False
    assert readiness.next_ready_node(state) == "task-T-2"


# ---------------------------------------------------------------------------
# AC-3: real agent failures do not count toward spawn cap
# ---------------------------------------------------------------------------


def test_failed_entry_with_input_tokens_gt_0_does_not_count_toward_spawn_cap(
    tmp_path, monkeypatch,
):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[_task_node("task-T-1", status="pending")],
        step_history=[
            _agent_failure_entry("task-T-1", "main", n) for n in range(1, 6)
        ],
    )
    state_path = _setup(tmp_path, monkeypatch, state, max_spawn_failures=3)
    action, code = dispatch(load_state(state_path), state_path)

    assert code == 0, "agent failures with tokens must not trigger spawn_failure_cap"
    assert action.get("step_id") == "task-T-1"
    assert action.get("reason") != "spawn_failure_cap"


def test_failed_entry_with_model_resolved_does_not_count_toward_spawn_cap(
    tmp_path, monkeypatch,
):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[_task_node("task-T-1", status="pending")],
        step_history=[
            _agent_failure_entry(
                "task-T-1",
                "main",
                n,
                input_tokens=0,
                output_tokens=0,
                model="claude-opus-4-7",
            )
            for n in range(1, 6)
        ],
    )
    state_path = _setup(tmp_path, monkeypatch, state, max_spawn_failures=3)
    action, code = dispatch(load_state(state_path), state_path)

    assert code == 0, "resolved model failures must not trigger spawn_failure_cap"
    assert action.get("reason") != "spawn_failure_cap"


# ---------------------------------------------------------------------------
# AC-4: orc-84 mixed storm + completion + storm
# ---------------------------------------------------------------------------


def test_orc84_fixture_with_storm_then_completion_then_more_storm_next_ready_node_skips_completed_step_id(
    tmp_path, monkeypatch,
):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[
            _task_node("task-T-1", status="pending"),
            _task_node("task-T-2", status="pending", depends_on=["task-T-1"]),
        ],
        step_history=_orc84_storm_completion_storm_history(),
    )
    state_path = _setup(tmp_path, monkeypatch, state)
    state = load_state(state_path)

    ready = readiness.next_ready_node(state)
    assert ready is not None
    assert ready != "task-T-1", (
        "completed task-T-1 must not be re-selected after post-completion spawn storm"
    )
    assert ready == "task-T-2"
