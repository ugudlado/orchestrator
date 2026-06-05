"""
Tests for dispatch.py resolved_allowed_tools injection.

- All 4 action-dict construction sites carry the resolved_allowed_tools key.
- Tools are documented in config/agents.yaml comments but not enforced at dispatch
  (resolver.load_agent_tools always returns None) → resolved_allowed_tools == [].
- Role unresolvable → stderr warning, resolved_allowed_tools == [], no exception.
- script step (no agent) with allowed_tools → stderr warning, resolved_allowed_tools == [].
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
    """A directory with a minimal plan.yaml for dispatch tests.

    Tests that need specific step IDs should call _write_plan_yaml(state_dir, ...).
    A default plan.yaml with an empty implement phase is pre-written for tests
    that don't care about step_context contents.
    """
    d = tmp_path / "state"
    d.mkdir()
    default_plan = {
        "feature": "test-change",
        "schema": "feature",
        "resolved_flags": {},
        "phases": [
            {
                "name": "implement",
                "goal": "Implement.",
                "steps": [],
            }
        ],
    }
    (d / "plan.yaml").write_text(yaml.safe_dump(default_plan, sort_keys=False))
    (d / "state.yaml").write_text(yaml.safe_dump({"change_id": "test-change"}))
    return d


def _write_contract(steps_dir, step_id: str, data: dict):
    (steps_dir / f"{step_id}.yaml").write_text(yaml.dump(data))


def _write_agent(agents_dir, agent_name: str, tools: list[str]):
    fm = "---\ntools:\n" + "".join(f"- {t}\n" for t in tools) + "---\n# {agent_name}\n"
    (agents_dir / f"{agent_name}.md").write_text(fm)


def _write_plan_yaml(state_dir, phase: str, step_ids: list) -> str:
    """Write a minimal plan.yaml next to state.yaml; return the state.yaml path string."""
    from pathlib import Path
    state_dir = Path(state_dir)
    plan = {
        "feature": "test-change",
        "schema": "feature",
        "resolved_flags": {},
        "phases": [
            {
                "name": phase,
                "goal": "Test phase.",
                "steps": [
                    {"id": sid, "agent": "developer", "goal": "Test.", "inputs": [],
                     "outputs": [], "rules": []}
                    for sid in step_ids
                ],
            }
        ],
    }
    (state_dir / "plan.yaml").write_text(yaml.safe_dump(plan, sort_keys=False))
    state_yaml = state_dir / "state.yaml"
    state_yaml.write_text(yaml.safe_dump({"change_id": "test-change", "phase": phase}))
    return str(state_yaml)


def _make_state(steps: list[str], phase: str = "implement") -> "State":
    """Build a minimal State for the given steps."""
    from orchestrator_next.parser import State
    return State(
        change_id="test-change",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={phase: {"active": steps}},
        step_history=[],
        raw={"change_id": "test-change"},
    )


def _make_state_with_inprogress(step_id: str, agent: str, phase: str = "implement") -> "State":
    """Build a State with one in_progress entry (triggers retry path)."""
    from orchestrator_next.parser import State, StepHistoryEntry
    entry = StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status="in_progress",
        agent=agent,
        attempt=1,
        started_at="2026-01-01T00:00:00Z",
        ended_at=None,
        usage={},
        escalation=None,
        raw={"step_id": step_id, "phase": phase, "status": "in_progress",
              "agent": agent, "attempt": 1},
    )
    return State(
        change_id="test-change",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={phase: {"active": [step_id]}},
        step_history=[entry],
        raw={"change_id": "test-change"},
    )


# ---------------------------------------------------------------------------
# T-3: resolved_allowed_tools in action dict — all 4 sites
# ---------------------------------------------------------------------------

class TestResolvedAllowedToolsInjection:

    def test_run_inline_no_run_has_resolved_allowed_tools(self, steps_dir, agents_dir, state_dir, monkeypatch):
        """run_inline (no run:) action dict has resolved_allowed_tools key."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep", "Glob", "Bash"])
        _write_contract(steps_dir, "my-step", {
            "id": "my-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
        })
        _write_plan_yaml(state_dir, "implement", ["my-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["my-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert "agent" in action  # ORC-45: agent key indicates agent path
        assert "resolved_allowed_tools" in action

    def test_run_step_has_resolved_allowed_tools(self, steps_dir, agents_dir, state_dir, monkeypatch):
        """run_step (contract.run set, not inline) action dict has resolved_allowed_tools key."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep", "Glob", "Bash"])
        _write_contract(steps_dir, "run-step", {
            "id": "run-step", "agent": "developer",
            "run": "scripts/run.sh",
            "instruction": "run thing", "inputs": [], "outputs": [],
        })
        _write_plan_yaml(state_dir, "implement", ["run-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["run-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert "agent" in action  # ORC-45: agent key indicates agent path
        assert "resolved_allowed_tools" in action

    def test_run_inline_with_run_has_resolved_allowed_tools(self, steps_dir, agents_dir, state_dir, monkeypatch):
        """run_inline (inline: true + run:) action dict has resolved_allowed_tools key."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_contract(steps_dir, "inline-run-step", {
            "id": "inline-run-step",
            "inline": True, "run": "scripts/inline.sh",
            "instruction": "inline thing", "inputs": [], "outputs": [],
        })
        _write_plan_yaml(state_dir, "implement", ["inline-run-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["inline-run-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        # Under ORC-45: inline:true + run: contracts without a real agent go to run: path
        # Script step (no agent) routes to run: path but resolved_allowed_tools=[]
        assert "resolved_allowed_tools" in action

    def test_resume_step_has_resolved_allowed_tools(self, steps_dir, agents_dir, state_dir, monkeypatch):
        """resume_step action dict has resolved_allowed_tools key."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep", "Glob", "Bash"])
        _write_contract(steps_dir, "retry-step", {
            "id": "retry-step", "agent": "developer",
            "instruction": "retry thing", "inputs": [], "outputs": [],
        })
        _write_plan_yaml(state_dir, "implement", ["retry-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state_with_inprogress("retry-step", "developer")
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert action.get("is_resume") is True  # ORC-45: check is_resume flag
        assert "resolved_allowed_tools" in action


# ---------------------------------------------------------------------------
# resolved_allowed_tools when tools are not enforced at dispatch
# ---------------------------------------------------------------------------

class TestResolvedAllowedToolsIntersection:

    def test_allowed_tools_subset_gives_empty_list(self, steps_dir, agents_dir, state_dir, monkeypatch, capsys):
        """allowed_tools declared but tools not enforced at dispatch → []."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep", "Glob", "Bash", "Edit", "Write"])
        _write_contract(steps_dir, "subset-step", {
            "id": "subset-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": ["Read", "Grep", "Glob", "Bash"],
        })
        _write_plan_yaml(state_dir, "implement", ["subset-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["subset-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert action["resolved_allowed_tools"] == []
        assert "not enforced" in capsys.readouterr().err

    def test_no_allowed_tools_gives_empty_list(self, steps_dir, agents_dir, state_dir, monkeypatch):
        """No allowed_tools declared → resolved_allowed_tools == [] (tools not enforced)."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Bash", "Grep"])
        _write_contract(steps_dir, "no-tools-step", {
            "id": "no-tools-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
        })
        _write_plan_yaml(state_dir, "implement", ["no-tools-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["no-tools-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert action["resolved_allowed_tools"] == []

    def test_empty_allowed_tools_identical_to_absent(self, steps_dir, agents_dir, state_dir, monkeypatch):
        """allowed_tools: [] → same as absent (both yield [])."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Bash", "Grep"])
        _write_contract(steps_dir, "empty-tools-step", {
            "id": "empty-tools-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": [],
        })
        _write_plan_yaml(state_dir, "implement", ["empty-tools-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["empty-tools-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert action["resolved_allowed_tools"] == []


# ---------------------------------------------------------------------------
# widening guard skipped when tools are not enforced at dispatch
# ---------------------------------------------------------------------------

class TestWideningGuard:

    def test_widening_does_not_raise_when_tools_not_enforced(
        self, steps_dir, agents_dir, monkeypatch, capsys,
    ):
        """allowed_tools widening is not checked when resolver returns None."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_agent(agents_dir, "developer", ["Read", "Grep"])
        _write_contract(steps_dir, "widen-step", {
            "id": "widen-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": ["Read", "NewTool"],
        })
        from orchestrator_next.dispatch import _resolve_allowed_tools
        from orchestrator_next.parser import _load_contract
        contract = _load_contract("widen-step", "")
        assert _resolve_allowed_tools(contract) == []
        assert "not enforced" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T-3: graceful degradation (AC-5, AC-6, FR-8, FR-9)
# ---------------------------------------------------------------------------

class TestGracefulDegradation:

    def test_role_unresolvable_warns_and_gives_empty_list(self, steps_dir, agents_dir, state_dir, monkeypatch, capsys):
        """Role tools unresolvable -> warning on stderr, resolved_allowed_tools == [], no exception (AC-5)."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        home = agents_dir.parent.parent / "empty_home"
        (home / ".claude" / "agents").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        (agents_dir / "unparsed-agent.md").write_text("---\nname: unparsed\n---\n")
        _write_contract(steps_dir, "missing-role-step", {
            "id": "missing-role-step", "agent": "unparsed-agent",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": ["Read"],
        })
        _write_plan_yaml(state_dir, "implement", ["missing-role-step"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["missing-role-step"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert action["resolved_allowed_tools"] == []
        captured = capsys.readouterr()
        assert captured.err != ""  # some warning was emitted

    def test_inline_with_allowed_tools_warns_and_gives_empty_list(self, steps_dir, agents_dir, state_dir, monkeypatch, capsys):
        """script step (no agent) with allowed_tools -> warning, resolved_allowed_tools == [], no exception (AC-6)."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(agents_dir.parent))
        _write_contract(steps_dir, "inline-with-tools", {
            "id": "inline-with-tools",
            "run": "scripts/inline-with-tools.sh",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": ["Read"],
        })
        _write_plan_yaml(state_dir, "implement", ["inline-with-tools"])
        from orchestrator_next.dispatch import dispatch
        state = _make_state(["inline-with-tools"])
        action, code = dispatch(state, str(state_dir / "state.yaml"))
        assert action["resolved_allowed_tools"] == []
        captured = capsys.readouterr()
        assert captured.err != ""  # warning about allowed_tools on inline step
