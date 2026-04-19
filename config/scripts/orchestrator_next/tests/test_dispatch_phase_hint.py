"""
Tests for dispatch.py phase-completion WARNING.

T-3: RED tests — verify dispatcher emits WARNING when completing a phase that is
not the last phase in workflow_plan:
- Non-terminal phase completion emits WARNING on stderr mentioning current/remaining phases.
- Terminal (last) phase completion emits no WARNING.
- Single-phase plan emits no WARNING.

These tests fail until T-4 injects the WARNING into dispatch.py.
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
# Helpers
# ---------------------------------------------------------------------------

def _completed_entry(step_id: str, phase: str):
    """Return a StepHistoryEntry with status='completed' for the given (phase, step_id)."""
    from orchestrator_next.parser import StepHistoryEntry
    return StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T01:00:00Z",
        usage={},
        escalation=None,
        raw={
            "step_id": step_id,
            "phase": phase,
            "status": "completed",
            "agent": "developer",
            "attempt": 1,
        },
    )


def _make_multi_phase_state(current_phase: str, phases: list[str], steps_per_phase: list[list[str]]):
    """
    Build a State with a multi-phase workflow_plan.

    All steps in `current_phase` have completed history entries so the
    dispatcher reaches the complete_workflow branch for that phase.
    Steps in other phases are present in active: but have no history.
    """
    from orchestrator_next.parser import State

    plan = {}
    for phase, steps in zip(phases, steps_per_phase):
        plan[phase] = {"active": steps}

    # Completed history for every step in the current phase only
    current_idx = phases.index(current_phase)
    history = []
    for step_id in steps_per_phase[current_idx]:
        history.append(_completed_entry(step_id, current_phase))

    return State(
        change_id="test-change",
        phase=current_phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan=plan,
        step_history=history,
        raw={"change_id": "test-change"},
    )


# ---------------------------------------------------------------------------
# T-3 Tests
# ---------------------------------------------------------------------------

class TestPhaseCompletionWarning:

    def test_warns_on_non_terminal_phase_completion(self, capsys):
        """
        When all steps in the current phase are complete and there are remaining
        phases, dispatch emits a WARNING on stderr mentioning the current phase
        and at least one remaining phase. The returned action is still complete_workflow.
        """
        from orchestrator_next.dispatch import dispatch

        state = _make_multi_phase_state(
            current_phase="specify",
            phases=["specify", "implement", "complete"],
            steps_per_phase=[["step-s1"], ["step-i1"], ["step-c1"]],
        )
        action, code = dispatch(state, "")
        captured = capsys.readouterr()

        assert "WARNING" in captured.err, "Expected WARNING on stderr"
        assert "specify" in captured.err, "Current phase name should appear in WARNING"
        # At least one remaining phase should be mentioned
        remaining_mentioned = "implement" in captured.err or "complete" in captured.err
        assert remaining_mentioned, "At least one remaining phase should appear in WARNING"
        assert action["action"] == "complete_workflow"

    def test_no_warning_on_terminal_phase(self, capsys):
        """
        When the current phase is the last phase in workflow_plan (all phases
        completed), no WARNING is emitted on stderr.
        """
        from orchestrator_next.dispatch import dispatch

        # All three phases have steps, but we're on 'complete' (last phase)
        # Steps for 'complete' are in history as completed
        state = _make_multi_phase_state(
            current_phase="complete",
            phases=["specify", "implement", "complete"],
            steps_per_phase=[["step-s1"], ["step-i1"], ["step-c1"]],
        )
        action, code = dispatch(state, "")
        captured = capsys.readouterr()

        # No phase-transition WARNING should appear
        phase_warning = any(
            "WARNING" in line and ("specify" in line or "implement" in line or "complete" in line)
            for line in captured.err.splitlines()
        )
        assert not phase_warning, f"Unexpected phase WARNING on stderr: {captured.err!r}"
        assert action["action"] == "complete_workflow"

    def test_no_warning_on_single_phase_plan(self, capsys):
        """
        When workflow_plan contains only one phase, completing it emits no WARNING.
        """
        from orchestrator_next.dispatch import dispatch

        state = _make_multi_phase_state(
            current_phase="implement",
            phases=["implement"],
            steps_per_phase=[["step-only"]],
        )
        action, code = dispatch(state, "")
        captured = capsys.readouterr()

        assert "WARNING" not in captured.err, (
            f"Unexpected WARNING for single-phase plan: {captured.err!r}"
        )
        assert action["action"] == "complete_workflow"
