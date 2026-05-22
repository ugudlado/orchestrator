"""ORC-75 T-3: Regression tests for agent: skill-runner dispatch and record.

Bug: run-learn-cycle.yaml declares agent: workflow-improver but its instruction
tells the driver to invoke /learn as a skill inline (driver context, not a
spawned sub-agent).  The fix renames the sentinel to skill-runner.

Two sub-tests:
(a) Dispatch: agent: skill-runner contract → dispatch() returns action with
    agent == "skill-runner" and an instruction field.
(b) Record: record() with status: completed, agent: "skill-runner", no usage
    tokens → exit 0 (usage guard skipped). Currently FAILS because record.py
    only exempts "inline", not "skill-runner".

Tests must FAIL before T-4 fix (record.py guard extension).
"""
from __future__ import annotations

import os
import sys
import textwrap

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.dispatch import dispatch  # noqa: E402
from orchestrator_next.record import record  # noqa: E402
from orchestrator_next.parser import load_state  # noqa: E402


# ---------------------------------------------------------------------------
# Contract stub for a skill-runner step
# ---------------------------------------------------------------------------

_CONTRACT_RUN_LEARN_CYCLE = textwrap.dedent("""\
    id: run-learn-cycle
    version: 1
    agent: skill-runner
    instruction: |
      Invoke /learn with the change ID:
      Skill({ skill: "learn", args: "<CHANGE_ID>" })
    rules:
      - Learning failure is non-blocking.
    inputs: []
    outputs:
      - learn_result
""")


# ---------------------------------------------------------------------------
# (a) Dispatch tests
# ---------------------------------------------------------------------------

class TestDispatchSkillRunner:
    """dispatch() must emit action with agent == "skill-runner"."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        (steps_dir / "run-learn-cycle.yaml").write_text(_CONTRACT_RUN_LEARN_CYCLE)
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))
        self.tmp_path = tmp_path

    def _make_state_yaml(self) -> str:
        """Write a state.yaml with run-learn-cycle as the next ready node."""
        state = {
            "change_id": "orc-75-skill-runner",
            "phase": "complete",
            "schema": "feature",
            "workflow_plan": {
                "complete": {
                    "nodes": [
                        {
                            "id": "run-learn-cycle",
                            "status": "pending",
                            "agent": "skill-runner",
                            "goal": "Invoke /learn",
                            "inputs": [],
                            "outputs": ["learn_result"],
                            "rules": [],
                        }
                    ],
                    "filtered": [],
                }
            },
            "step_history": [],
        }
        path = self.tmp_path / "state.yaml"
        path.write_text(yaml.safe_dump(state, sort_keys=False))
        return str(path)

    def test_dispatch_skill_runner_agent(self):
        """dispatch() must yield action["agent"] == "skill-runner"."""
        state_path = self._make_state_yaml()
        state = load_state(state_path)
        action, exit_code = dispatch(state, state_path)
        assert exit_code == 0, f"Expected exit 0, got {exit_code}"
        assert action.get("agent") == "skill-runner", (
            f"Expected agent 'skill-runner', got '{action.get('agent')}'"
        )

    def test_dispatch_skill_runner_carries_instruction(self):
        """dispatch() action must carry the instruction from the contract."""
        state_path = self._make_state_yaml()
        state = load_state(state_path)
        action, exit_code = dispatch(state, state_path)
        assert exit_code == 0
        assert "instruction" in action, "action must carry 'instruction'"
        assert len(action["instruction"]) > 0, "instruction must not be empty"

    def test_dispatch_skill_runner_carries_step_id(self):
        """dispatch() action must carry step_id and phase."""
        state_path = self._make_state_yaml()
        state = load_state(state_path)
        action, exit_code = dispatch(state, state_path)
        assert exit_code == 0
        assert action.get("step_id") == "run-learn-cycle"
        assert action.get("phase") == "complete"


# ---------------------------------------------------------------------------
# (b) Record tests — must FAIL before T-4 fix
# ---------------------------------------------------------------------------

class TestRecordSkillRunner:
    """record() must accept skill-runner steps with no usage tokens."""

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Isolate from real contract files."""
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))
        self.tmp_path = tmp_path

    def _minimal_state(self) -> str:
        state = {
            "change_id": "orc-75-skill-runner",
            "phase": "complete",
            "workflow_plan": {
                "complete": {
                    "nodes": [
                        {
                            "id": "run-learn-cycle",
                            "status": "in_progress",
                            "agent": "skill-runner",
                            "goal": "Invoke /learn",
                            "inputs": [],
                            "outputs": ["learn_result"],
                            "rules": [],
                        }
                    ],
                    "filtered": [],
                }
            },
            "step_history": [
                {
                    "step_id": "run-learn-cycle",
                    "phase": "complete",
                    "status": "in_progress",
                    "agent": "skill-runner",
                    "attempt": 1,
                    "started_at": "2026-05-22T10:00:00Z",
                    "ended_at": None,
                }
            ],
        }
        path = self.tmp_path / "state.yaml"
        path.write_text(yaml.safe_dump(state, sort_keys=False))
        return str(path)

    def test_record_skill_runner_no_usage_tokens_exits_0(self):
        """skill-runner step completed with no usage tokens must exit 0.

        This test FAILS before T-4 fix: record.py rejects any non-inline
        agent with no usage tokens (agent_step_missing_usage, exit 3).
        """
        state_path = self._minimal_state()
        payload = {
            "step_id": "run-learn-cycle",
            "phase": "complete",
            "status": "completed",
            "agent": "skill-runner",
            "outputs": {"learn_result": {"learn_completed": True}},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, (
            f"Expected exit 0 for skill-runner step with no usage, "
            f"got exit_code={exit_code}, reason={result.get('reason')!r}. "
            "Bug: record.py does not exempt 'skill-runner' from usage guard."
        )

    def test_record_skill_runner_no_agent_task_result_exits_0(self):
        """skill-runner step completed with no agent_task_result must exit 0."""
        state_path = self._minimal_state()
        payload = {
            "step_id": "run-learn-cycle",
            "phase": "complete",
            "status": "completed",
            "agent": "skill-runner",
            "outputs": {"learn_result": {"learn_skipped": True}},
            # no usage, no agent_task_result
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, (
            f"Expected exit 0 for skill-runner step, "
            f"got exit_code={exit_code}, reason={result.get('reason')!r}."
        )

    def test_record_inline_still_exempt(self):
        """Existing inline exemption must remain intact after T-4 fix."""
        state = {
            "change_id": "test-inline",
            "phase": "specify",
            "workflow_plan": {
                "specify": {"active": ["some-step"], "filtered": []},
            },
            "step_history": [],
        }
        path = self.tmp_path / "state_inline.yaml"
        path.write_text(yaml.safe_dump(state, sort_keys=False))
        payload = {
            "step_id": "some-step",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "usage": {},
        }
        result, exit_code = record(str(path), payload)
        assert exit_code == 0, (
            f"Inline exemption broken: exit_code={exit_code}, result={result}"
        )
