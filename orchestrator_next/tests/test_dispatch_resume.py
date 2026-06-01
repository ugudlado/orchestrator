"""
Tests for the resume_step dispatch branch — FR-3, FR-10, AC-2.

Scenarios:
  (a) State has one in_progress entry (attempt=1). dispatch() returns
      action='resume_step' with is_resume=True and attempt=1 (ORIGINAL,
      unchanged — NOT _compute_attempt's max+1).
  (b) Original started_at from the in_progress entry is preserved in the
      returned action's started_at field.
  (c) All contract fields (inputs, env, step_context, resolved_allowed_tools,
      run, instruction, rules) populated identically to run_step for the
      same step_id.

These tests are RED until T-6 replaces the retry_step branch with resume_step.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.parser import State, StepHistoryEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def steps_dir(tmp_path):
    d = tmp_path / "steps"
    d.mkdir()
    return d


@pytest.fixture()
def agents_dir(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def set_steps_override(steps_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


@pytest.fixture()
def state_dir(tmp_path):
    """A directory with a minimal state.yaml for dispatch tests."""
    d = tmp_path / "state"
    d.mkdir()
    (d / "state.yaml").write_text(yaml.safe_dump({"change_id": "test-resume"}))
    return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_contract(steps_dir, step_id: str, data: dict):
    (steps_dir / f"{step_id}.yaml").write_text(yaml.dump(data))


def _write_agent(agents_dir, agent_name: str, tools: list):
    fm = f"---\ntools:\n" + "".join(f"- {t}\n" for t in tools) + "---\n# {agent_name}\n"
    (agents_dir / f"{agent_name}.md").write_text(fm)


def _write_plan_yaml(state_dir, phase: str, step_ids: list) -> str:
    """Write a minimal plan.yaml next to state.yaml; return the state.yaml path."""
    from pathlib import Path
    state_dir = Path(state_dir)
    plan = {
        "feature": "test-resume",
        "schema": "feature",
        "resolved_flags": {},
        "phases": [
            {
                "name": phase,
                "goal": "Test resume phase.",
                "steps": [
                    {
                        "id": sid, "agent": "developer",
                        "goal": "Test step.", "inputs": [], "outputs": [], "rules": [],
                    }
                    for sid in step_ids
                ],
            }
        ],
    }
    (state_dir / "plan.yaml").write_text(yaml.safe_dump(plan, sort_keys=False))
    state_yaml = state_dir / "state.yaml"
    state_yaml.write_text(yaml.safe_dump({"change_id": "test-resume", "phase": phase}))
    return str(state_yaml)


def _make_state_with_inprogress(
    step_id: str,
    agent: str,
    phase: str = "implement",
    attempt: int = 1,
    started_at: str = "2026-01-01T00:00:00Z",
) -> State:
    """Build a State with one in_progress entry at the given attempt."""
    entry = StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status="in_progress",
        agent=agent,
        attempt=attempt,
        started_at=started_at,
        ended_at=None,
        usage={},
        escalation=None,
        raw={
            "step_id": step_id, "phase": phase, "status": "in_progress",
            "agent": agent, "attempt": attempt, "started_at": started_at,
        },
    )
    return State(
        change_id="test-resume",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={phase: {"active": [step_id]}},
        step_history=[entry],
        raw={"change_id": "test-resume"},
    )


# ---------------------------------------------------------------------------
# Tests — resume_step action shape
# ---------------------------------------------------------------------------

class TestResumeStepActionShape:

    def test_resume_returns_resume_step_action(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """
        (a) State has one in_progress entry (attempt=1).
        dispatch() must return action='resume_step' with is_resume=True.
        """
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep"])
        _write_contract(steps_dir, "my-resume-step", {
            "id": "my-resume-step", "agent": "developer",
            "instruction": "Resume the work.", "inputs": [], "outputs": ["artifact"],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["my-resume-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("my-resume-step", "developer")
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action, (
            "Expected is_resume=True and agent key (ORC-45: no action field)"
        )
        assert action.get("is_resume") is True, (
            f"Expected is_resume=True, got: {action.get('is_resume')!r}"
        )
        assert code == 0

    def test_resume_returns_same_attempt_and_is_resume_flag(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """
        AC-2: attempt=1 in_progress entry → resume returns attempt=1 (unchanged).
        Resume MUST NOT call _compute_attempt (which would return 2 = max+1).
        """
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep"])
        _write_contract(steps_dir, "my-resume-step", {
            "id": "my-resume-step", "agent": "developer",
            "instruction": "Resume the work.", "inputs": [], "outputs": [],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["my-resume-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("my-resume-step", "developer", attempt=1)
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        assert action.get("is_resume") is True
        assert action["attempt"] == 1, (
            f"Expected attempt=1 (preserved), got: {action['attempt']} "
            "(resume must NOT call _compute_attempt which returns max+1)"
        )

    def test_resume_preserves_original_started_at(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """
        (b) The started_at from the in_progress entry is preserved in action['started_at'].
        """
        original_started_at = "2026-03-15T08:00:00Z"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read"])
        _write_contract(steps_dir, "my-resume-step", {
            "id": "my-resume-step", "agent": "developer",
            "instruction": "Resume.", "inputs": [], "outputs": [],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["my-resume-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress(
            "my-resume-step", "developer", started_at=original_started_at
        )
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        assert action.get("started_at") == original_started_at, (
            f"Expected started_at={original_started_at!r}, got: {action.get('started_at')!r}"
        )

    def test_resume_attempt_higher_than_one_preserved(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """
        (a extension) in_progress entry with attempt=2 → resume returns attempt=2 unchanged.
        This mirrors state-crash-midstep.yaml where history has [1:completed, 2:failed, 2:in_progress].
        """
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read"])
        _write_contract(steps_dir, "my-resume-step", {
            "id": "my-resume-step", "agent": "developer",
            "instruction": "Resume.", "inputs": [], "outputs": [],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["my-resume-step"])
        from orchestrator_next.dispatch import dispatch
        # Build state that mirrors crash-midstep: [1:completed, 2:failed, 2:in_progress]
        completed_entry = StepHistoryEntry(
            step_id="my-resume-step",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T01:00:00Z",
            usage={},
            escalation=None,
            raw={"step_id": "my-resume-step", "phase": "implement", "status": "completed",
                 "agent": "developer", "attempt": 1},
        )
        failed_entry = StepHistoryEntry(
            step_id="my-resume-step",
            phase="implement",
            status="failed",
            agent="developer",
            attempt=2,
            started_at="2026-01-01T02:00:00Z",
            ended_at="2026-01-01T03:00:00Z",
            usage={},
            escalation=None,
            raw={"step_id": "my-resume-step", "phase": "implement", "status": "failed",
                 "agent": "developer", "attempt": 2},
        )
        inprogress_entry = StepHistoryEntry(
            step_id="my-resume-step",
            phase="implement",
            status="in_progress",
            agent="developer",
            attempt=2,
            started_at="2026-01-01T04:00:00Z",
            ended_at=None,
            usage={},
            escalation=None,
            raw={"step_id": "my-resume-step", "phase": "implement", "status": "in_progress",
                 "agent": "developer", "attempt": 2},
        )
        state = State(
            change_id="test-resume",
            phase="implement",
            repo_root="/repo",
            workflow_dir="/workflow",
            workflow_plan={"implement": {"active": ["my-resume-step"]}},
            step_history=[completed_entry, failed_entry, inprogress_entry],
            raw={"change_id": "test-resume"},
        )
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        assert action["attempt"] == 2, (
            f"Expected attempt=2 (preserved from in_progress entry), got: {action['attempt']} "
            "(must NOT return _compute_attempt which would give max(1,2,2)+1=3)"
        )
        assert action.get("is_resume") is True


class TestResumeStepContractFields:
    """
    (c) Contract fields must be populated identically to run_step for the same step_id.
    """

    def test_resume_step_has_required_contract_fields(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """All fields required for driver re-spawn are present in resume_step action."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep", "Glob"])
        _write_contract(steps_dir, "contract-step", {
            "id": "contract-step", "agent": "developer",
            "instruction": "Do the contract step work.",
            "rules": ["rule-one", "rule-two"],
            "inputs": [], "outputs": ["out1"],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["contract-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("contract-step", "developer")
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        # All fields required by FR-3 for driver re-spawn
        for field in ("inputs", "env", "step_context", "resolved_allowed_tools",
                      "instruction", "rules"):
            assert field in action, f"Missing required field: {field!r}"
        # ORC-45: run: is not included in agent-path response (agent key presence = agent path)
        # run: is only present in inline-script path responses
        # env block has all ORCHESTRATOR_* vars
        env = action["env"]
        for key in ("ORCHESTRATOR_CHANGE_ID", "ORCHESTRATOR_PHASE", "ORCHESTRATOR_STEP_ID",
                    "ORCHESTRATOR_ATTEMPT", "ORCHESTRATOR_WORKFLOW_DIR", "ORCHESTRATOR_REPO_ROOT"):
            assert key in env, f"Missing env var: {key!r}"

    def test_resume_step_resolved_allowed_tools_present(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """resolved_allowed_tools key is present on resume_step (tools not enforced → [])."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep", "Glob", "Bash"])
        _write_contract(steps_dir, "tools-step", {
            "id": "tools-step", "agent": "developer",
            "instruction": "Do tools work.", "inputs": [], "outputs": [],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["tools-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("tools-step", "developer")
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        assert action.get("resolved_allowed_tools") == []

    def test_resume_step_env_reflects_preserved_attempt(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """ORCHESTRATOR_ATTEMPT in env must match the preserved (not bumped) attempt."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read"])
        _write_contract(steps_dir, "env-step", {
            "id": "env-step", "agent": "developer",
            "instruction": "Check env.", "inputs": [], "outputs": [],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["env-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("env-step", "developer", attempt=1)
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        assert action["attempt"] == 1
        assert action["env"]["ORCHESTRATOR_ATTEMPT"] == "1", (
            f"ORCHESTRATOR_ATTEMPT must reflect preserved attempt=1, "
            f"got: {action['env']['ORCHESTRATOR_ATTEMPT']!r}"
        )

    def test_resume_step_has_step_context_from_plan(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """step_context is populated from plan.yaml for the in_progress step_id."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read"])
        _write_contract(steps_dir, "ctx-step", {
            "id": "ctx-step", "agent": "developer",
            "instruction": "Context step.", "inputs": [], "outputs": [],
        })
        state_yaml_path = _write_plan_yaml(state_dir, "implement", ["ctx-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("ctx-step", "developer")
        action, code = dispatch(state, state_yaml_path)

        assert action.get("is_resume") is True and "agent" in action  # ORC-45: no action field
        assert "step_context" in action
        assert action["step_context"].get("id") == "ctx-step", (
            f"step_context must identify the resumed step, got: {action['step_context']}"
        )


# ---------------------------------------------------------------------------
# T-13 — Driver resume-log integration test (subprocess-level)
# ---------------------------------------------------------------------------
#
# The CLI's responsibility ends at emitting action=resume_step.
# The literal "RESUMING step <id> (attempt <N>)" log line is emitted at the
# driver (prompt-loop) layer, not by bin/orchestrator itself. That log emission
# is enforced by SKILL.md prose contract. Testing it from Python would require
# mocking the driver loop — overkill. Instead we verify the CLI emits the
# correct JSON action that the driver interprets as a resume signal.

import json as _json
import os as _os
import shutil as _shutil
import subprocess as _subprocess
import sys as _sys
import textwrap as _textwrap
import unittest as _unittest

_HERE_SUB = _os.path.dirname(_os.path.abspath(__file__))
_WORKTREE_ROOT_SUB = _os.path.abspath(_os.path.join(_HERE_SUB, "..", ".."))
_SCRIPTS_DIR_SUB = _os.path.join(_WORKTREE_ROOT_SUB, "config", "scripts")
_BIN_ORCHESTRATOR_SUB = _os.path.join(_os.path.expanduser("~"), ".local", "bin", "orchestrator")
_STEP_CONTRACTS_DIR_SUB = _os.path.join(
    _HERE_SUB, "..", "..", "tests", "fixtures", "step_contracts"
)

# Minimal plan.yaml template (two steps so T-14 successor test can advance).
_PLAN_YAML_TWO_STEPS = _textwrap.dedent("""\
    phases:
    - name: implement
      steps:
      - id: {first_step_id}
        agent: {first_agent}
        goal: First test step.
        inputs: []
        outputs: []
        rules: []
      - id: {second_step_id}
        agent: {second_agent}
        goal: Second test step.
        inputs: []
        outputs: []
        rules: []
""")

_PLAN_YAML_ONE_STEP = _textwrap.dedent("""\
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


def _write_state_yaml_sub(directory: str, content: str) -> str:
    state_path = _os.path.join(directory, "state.yaml")
    with open(state_path, "w") as f:
        f.write(_textwrap.dedent(content))
    return state_path


def _run_next_sub(
    state_yaml_path: str,
) -> "_subprocess.CompletedProcess[str]":
    """Invoke bin/orchestrator next."""
    env = _os.environ.copy()
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR_SUB
    env["PYTHONPATH"] = _SCRIPTS_DIR_SUB
    env.pop("ORCHESTRATOR_HOME", None)
    env.pop("METRICS_DB", None)
    return _subprocess.run(
        [_sys.executable, _BIN_ORCHESTRATOR_SUB, "next", state_yaml_path],
        capture_output=True,
        text=True,
        env=env,
    )


def _load_state_yaml_sub(state_yaml_path: str) -> dict:
    import yaml as _yaml  # noqa: PLC0415
    with open(state_yaml_path) as f:
        return _yaml.safe_load(f) or {}


class TestResumeLogDriverContract(_unittest.TestCase):
    """AC-9 — driver-layer "RESUMING step" log contract.

    The SKILL.md driver loop is prose, not code. To make AC-9 executable, we
    ship a minimal fixture script at tests/fixtures/resume_log_driver.py that
    implements exactly the log contract from SKILL.md. These tests invoke that
    fixture as a subprocess with a resume_step payload on stdin and assert the
    log fires to stderr — including on autopilot runs.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path as _Path
        cls._FIXTURE = str(_Path(__file__).parent / "fixtures" / "resume_log_driver.py")

    def _run_fixture(self, payload, autopilot=False):
        env = os.environ.copy()
        env["AUTOPILOT"] = "true" if autopilot else "false"
        return _subprocess.run(
            [sys.executable, self._FIXTURE],
            input=_json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def test_resume_step_emits_resuming_log(self):
        result = self._run_fixture({
            "is_resume": True,  # ORC-45: check is_resume instead of action field,
            "step_id": "design-and-draft-artifacts",
            "attempt": 2,
        })
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(
            "RESUMING step design-and-draft-artifacts (attempt 2)",
            result.stderr,
            msg=f"expected RESUMING log in stderr, got: {result.stderr!r}",
        )

    def test_resume_step_log_fires_on_autopilot(self):
        """AC-9 explicit: the log MUST fire even on autopilot runs."""
        result = self._run_fixture(
            {"is_resume": True, "agent": "developer", "step_id": "execute-next-task", "attempt": 1},
            autopilot=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("RESUMING step execute-next-task (attempt 1)", result.stderr)

    def test_non_resume_action_emits_no_resuming_log(self):
        """Negative: run_step etc. must NOT produce the RESUMING log."""
        result = self._run_fixture({
            "agent": "developer",
            "step_id": "explore",
            "attempt": 1,
        })
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("RESUMING step", result.stderr)
