"""
T-1: Failing tests for path-3 dispatch elimination (ORC-45).

These tests FAIL against current dispatch.py and PASS after T-2.

Scenarios:
  (a) dispatch.py raises ContractDispatchError (exit 3) when a contract has
      neither `agent:` nor `run:`.
  (b) When contract.agent is set, response JSON has `agent` key and NO `action` key.
  (c) When all steps are done, `orchestrator next` exits 1 with no JSON on stdout.
  (d) When last step is blocked, `orchestrator next` exits 2 with no JSON on stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_SCRIPTS_DIR = os.path.join(_WORKTREE_ROOT, "config", "scripts")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_state_yaml(directory: str, content: str) -> str:
    state_path = os.path.join(directory, "state.yaml")
    with open(state_path, "w") as f:
        f.write(textwrap.dedent(content))
    return state_path


def _write_plan_yaml(directory: str, step_id: str, agent: str = "developer") -> str:
    plan = textwrap.dedent(f"""\
        phases:
        - name: implement
          steps:
          - id: {step_id}
            agent: {agent}
            goal: Test step.
            inputs: []
            outputs: []
            rules: []
    """)
    plan_path = os.path.join(directory, "plan.yaml")
    with open(plan_path, "w") as f:
        f.write(plan)
    return plan_path


def _write_contract_yaml(steps_dir: str, step_id: str, content: str) -> str:
    path = os.path.join(steps_dir, f"{step_id}.yaml")
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path


def _run_next(state_yaml_path: str, steps_dir: str, tmp_dir: str) -> subprocess.CompletedProcess:
    metrics_db = os.path.join(tmp_dir, "metrics.duckdb")
    env = {
        **os.environ,
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": steps_dir,
        "METRICS_DB": metrics_db,
    }
    return subprocess.run(
        [_BIN_ORCHESTRATOR, "next", state_yaml_path],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# (a) ContractDispatchError when contract has neither agent: nor run:
# ---------------------------------------------------------------------------

def test_contract_missing_agent_and_run_exits_3(tmp_path):
    """dispatch raises ContractDispatchError when contract has neither agent: nor run:."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    # Contract with no agent: and no run: -- path-3 territory
    _write_contract_yaml(str(steps_dir), "my-step", """\
        id: my-step
        version: 1
        instruction: Do something.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: test-no-path3
        phase: implement
        repo_root: /tmp/test-repo
        workflow_dir: /tmp
        flags: {}
        step_history: []
        workflow_plan:
          implement:
            active:
              - my-step
    """)
    _write_plan_yaml(str(tmp_path), "my-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 3, (
        f"Expected exit 3 (ContractDispatchError), got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "step_contract_missing_run" in result.stderr, (
        f"Expected 'step_contract_missing_run' in stderr, got: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# (b) Agent contract: response has `agent` key and NO `action` key
# ---------------------------------------------------------------------------

def test_agent_contract_response_has_agent_no_action(tmp_path):
    """When contract.agent is set, response JSON has agent key and NO action key."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    _write_contract_yaml(str(steps_dir), "my-step", """\
        id: my-step
        version: 1
        agent: developer
        instruction: Do something.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: test-agent-no-action
        phase: implement
        repo_root: /tmp/test-repo
        workflow_dir: /tmp
        flags: {}
        step_history: []
        workflow_plan:
          implement:
            active:
              - my-step
    """)
    _write_plan_yaml(str(tmp_path), "my-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    stdout = result.stdout.strip()
    assert stdout, "Expected JSON on stdout for agent contract"
    action = json.loads(stdout)

    assert "agent" in action, f"Expected 'agent' key in response, got keys: {list(action.keys())}"
    assert action["agent"] == "developer", f"Expected agent=developer, got: {action['agent']}"
    assert "action" not in action, (
        f"Expected NO 'action' key in response, but found action={action.get('action')!r}\n"
        f"Full response: {action}"
    )


# ---------------------------------------------------------------------------
# (c) All steps done -- exit 1, no JSON on stdout
# ---------------------------------------------------------------------------

def test_all_steps_done_exits_1_no_json(tmp_path):
    """When all steps are done, orchestrator next exits 1 with no JSON on stdout."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    _write_contract_yaml(str(steps_dir), "my-step", """\
        id: my-step
        version: 1
        agent: developer
        instruction: Do something.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: test-complete
        phase: implement
        repo_root: /tmp/test-repo
        workflow_dir: /tmp
        flags: {}
        step_history:
          - step_id: my-step
            phase: implement
            status: completed
            agent: developer
            attempt: 1
        workflow_plan:
          implement:
            active:
              - my-step
    """)
    _write_plan_yaml(str(tmp_path), "my-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 1, (
        f"Expected exit 1 (complete_workflow), got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    stdout = result.stdout.strip()
    assert not stdout, (
        f"Expected no JSON on stdout when workflow complete, got: {stdout!r}"
    )


# ---------------------------------------------------------------------------
# (d) Last step blocked -- exit 2, no JSON on stdout
# ---------------------------------------------------------------------------

def test_blocked_step_exits_2_no_json(tmp_path):
    """When last step is blocked, orchestrator next exits 2 with no JSON on stdout."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    _write_contract_yaml(str(steps_dir), "my-step", """\
        id: my-step
        version: 1
        agent: developer
        instruction: Do something.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: test-blocked
        phase: implement
        repo_root: /tmp/test-repo
        workflow_dir: /tmp
        flags: {}
        step_history:
          - step_id: my-step
            phase: implement
            status: blocked
            agent: developer
            attempt: 1
        workflow_plan:
          implement:
            active:
              - my-step
    """)
    _write_plan_yaml(str(tmp_path), "my-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 2, (
        f"Expected exit 2 (blocked), got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    stdout = result.stdout.strip()
    assert not stdout, (
        f"Expected no JSON on stdout when blocked, got: {stdout!r}"
    )
