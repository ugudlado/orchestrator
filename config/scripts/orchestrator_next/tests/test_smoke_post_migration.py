"""
T-11: Smoke tests post-migration (ORC-45).

(a) orchestrator next on a Category A fixture (run: contract) -> exit 0, no JSON on stdout.
(b) orchestrator next on a Category C fixture (agent: discoverer) -> exit 0, JSON has
    `agent: discoverer`, no `action` key.
(c) orchestrator next on a fixture with all steps done -> exit 1, no JSON.
(d) orchestrator next on a fixture contract missing both agent: and run: -> exit 3,
    stderr matches `step_contract_missing_run`.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")


def _write_state_yaml(directory, content):
    path = os.path.join(directory, "state.yaml")
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path


def _write_plan_yaml(directory, step_id, agent="developer"):
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
    path = os.path.join(directory, "plan.yaml")
    with open(path, "w") as f:
        f.write(plan)
    return path


def _write_contract_yaml(steps_dir, step_id, content):
    path = os.path.join(str(steps_dir), f"{step_id}.yaml")
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path


def _run_next(state_yaml_path, steps_dir, tmp_dir):
    metrics_db = os.path.join(tmp_dir, "metrics.duckdb")
    env = {
        **os.environ,
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": str(steps_dir),
        "METRICS_DB": metrics_db,
    }
    return subprocess.run(
        [_BIN_ORCHESTRATOR, "next", state_yaml_path],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# (a) Category A fixture (run: contract) -> exit 0, no JSON on stdout
# ---------------------------------------------------------------------------

def test_smoke_run_contract_exit_0_no_json(tmp_path):
    """Category A contract (run:) -> CLI executes script, exit 0, no JSON on stdout."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    # Create a trivial inline script that just echoes a message
    script_path = tmp_path / "test-run.sh"
    script_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho '[test] inline script ran' >&2\n")
    script_path.chmod(0o755)

    _write_contract_yaml(str(steps_dir), "my-run-step", f"""\
        id: my-run-step
        version: 2
        run: {script_path}
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: smoke-run-a
        phase: implement
        repo_root: /tmp/smoke-repo
        workflow_dir: /tmp
        flags: {}
        step_history: []
        workflow_plan:
          implement:
            active:
              - my-run-step
    """)
    _write_plan_yaml(str(tmp_path), "my-run-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 0, (
        f"Expected exit 0 for run: contract, got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    stdout = result.stdout.strip()
    assert not stdout, (
        f"Expected no JSON on stdout for run: contract, got: {stdout!r}"
    )


# ---------------------------------------------------------------------------
# (b) Category C fixture (agent: discoverer) -> exit 0, JSON has agent, no action
# ---------------------------------------------------------------------------

def test_smoke_agent_contract_exit_0_json_has_agent_no_action(tmp_path):
    """Category C contract (agent: discoverer) -> exit 0, JSON has agent key, no action key."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    _write_contract_yaml(str(steps_dir), "my-agent-step", """\
        id: my-agent-step
        version: 3
        agent: discoverer
        instruction: Discover something important.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: smoke-agent-b
        phase: implement
        repo_root: /tmp/smoke-repo
        workflow_dir: /tmp
        flags: {}
        step_history: []
        workflow_plan:
          implement:
            active:
              - my-agent-step
    """)
    _write_plan_yaml(str(tmp_path), "my-agent-step", agent="discoverer")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    stdout = result.stdout.strip()
    assert stdout, "Expected JSON on stdout for agent contract"

    action = json.loads(stdout)
    assert "agent" in action, f"Expected 'agent' key in response, got: {list(action.keys())}"
    assert action["agent"] == "discoverer", f"Expected agent=discoverer, got: {action['agent']}"
    assert "action" not in action, (
        f"Expected NO 'action' key in response, but found action={action.get('action')!r}"
    )


# ---------------------------------------------------------------------------
# (c) All steps done -> exit 1, no JSON on stdout
# ---------------------------------------------------------------------------

def test_smoke_all_done_exit_1_no_json(tmp_path):
    """All steps done -> exit 1, no JSON on stdout."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    _write_contract_yaml(str(steps_dir), "my-done-step", """\
        id: my-done-step
        version: 2
        agent: developer
        instruction: Something already done.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: smoke-done-c
        phase: implement
        repo_root: /tmp/smoke-repo
        workflow_dir: /tmp
        flags: {}
        step_history:
          - step_id: my-done-step
            phase: implement
            status: completed
            agent: developer
            attempt: 1
        workflow_plan:
          implement:
            active:
              - my-done-step
    """)
    _write_plan_yaml(str(tmp_path), "my-done-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 1, (
        f"Expected exit 1 (complete), got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    stdout = result.stdout.strip()
    assert not stdout, f"Expected no JSON on stdout when complete, got: {stdout!r}"


# ---------------------------------------------------------------------------
# (d) Contract missing both agent: and run: -> exit 3, stderr has step_contract_missing_run
# ---------------------------------------------------------------------------

def test_smoke_missing_agent_and_run_exit_3(tmp_path):
    """Contract missing both agent: and run: -> exit 3, stderr has step_contract_missing_run."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    _write_contract_yaml(str(steps_dir), "my-legacy-step", """\
        id: my-legacy-step
        version: 1
        instruction: A legacy step with no agent or run.
        inputs: []
        outputs: []
        rules: []
    """)

    state_yaml_path = _write_state_yaml(str(tmp_path), """\
        schema: feature
        change_id: smoke-missing-d
        phase: implement
        repo_root: /tmp/smoke-repo
        workflow_dir: /tmp
        flags: {}
        step_history: []
        workflow_plan:
          implement:
            active:
              - my-legacy-step
    """)
    _write_plan_yaml(str(tmp_path), "my-legacy-step")

    result = _run_next(state_yaml_path, str(steps_dir), str(tmp_path))

    assert result.returncode == 3, (
        f"Expected exit 3 (ContractDispatchError), got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "step_contract_missing_run" in result.stderr, (
        f"Expected 'step_contract_missing_run' in stderr, got: {result.stderr!r}"
    )
