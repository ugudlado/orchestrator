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

        assert action["action"] == "resume_step", (
            f"Expected action='resume_step', got: {action['action']!r}"
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

        assert action["action"] == "resume_step"
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

        assert action["action"] == "resume_step"
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

        assert action["action"] == "resume_step"
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

        assert action["action"] == "resume_step"
        # All fields required by FR-3 for driver re-spawn
        for field in ("inputs", "env", "step_context", "resolved_allowed_tools",
                      "instruction", "rules"):
            assert field in action, f"Missing required field: {field!r}"
        # run is present (possibly None per design.md pseudocode)
        assert "run" in action, "Missing 'run' key — design.md pseudocode sets it unconditionally"
        # env block has all ORCHESTRATOR_* vars
        env = action["env"]
        for key in ("ORCHESTRATOR_CHANGE_ID", "ORCHESTRATOR_PHASE", "ORCHESTRATOR_STEP_ID",
                    "ORCHESTRATOR_ATTEMPT", "ORCHESTRATOR_WORKFLOW_DIR", "ORCHESTRATOR_REPO_ROOT"):
            assert key in env, f"Missing env var: {key!r}"

    def test_resume_step_resolved_allowed_tools_populated(
        self, steps_dir, agents_dir, state_dir, monkeypatch
    ):
        """resolved_allowed_tools is populated from the agent role on resume_step."""
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

        assert action["action"] == "resume_step"
        assert "resolved_allowed_tools" in action
        assert sorted(action["resolved_allowed_tools"]) == ["Bash", "Glob", "Grep", "Read"]

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

        assert action["action"] == "resume_step"
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

        assert action["action"] == "resume_step"
        assert "step_context" in action
        assert action["step_context"].get("id") == "ctx-step", (
            f"step_context must identify the resumed step, got: {action['step_context']}"
        )
