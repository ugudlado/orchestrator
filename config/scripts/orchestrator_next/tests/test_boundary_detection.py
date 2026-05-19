"""T-3 (RED) / T-4 (GREEN): _detect_boundary pure function in record.py.

Tests cover FR-4 — boundary detection is small, branchy, and critical; 100% coverage.

Cases:
  (a) NONE when status != 'completed'
  (b) NONE when step_id is not last in phase active list
  (c) PHASE when step_id is last but phase is not last in workflow_plan keys
  (d) FEATURE when step_id is last AND phase is last in workflow_plan keys
  (e) NONE when workflow_plan has empty phase block
  (f) NONE when active list is empty
  (g) FEATURE: last key detection uses insertion order (list of keys)
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _detect_boundary, BoundaryKind  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _plan_two_phases():
    """workflow_plan with 'specify' and 'implement' phases."""
    return {
        "specify": {
            "active": ["prepare-step", "specify-step"],
            "filtered": [],
        },
        "implement": {
            "active": ["build-step", "test-step"],
            "filtered": [],
        },
    }


def _plan_single_phase():
    """workflow_plan with only one phase — any last step is a FEATURE boundary."""
    return {
        "implement": {
            "active": ["only-step"],
            "filtered": [],
        },
    }


# ---------------------------------------------------------------------------
# (a) NONE when status != 'completed'
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["recovered", "abandoned", "in_progress", "failed"])
def test_none_when_not_completed(status):
    """Any status other than 'completed' → BoundaryKind.NONE regardless of step position."""
    plan = _plan_two_phases()
    result = _detect_boundary(plan, "implement", "test-step", status)
    assert result == BoundaryKind.NONE, (
        f"Expected NONE for status={status!r}, got {result}"
    )


# ---------------------------------------------------------------------------
# (b) NONE when step_id is not the last in phase active
# ---------------------------------------------------------------------------

def test_none_when_step_not_last_in_phase():
    """step_id is in the middle of active — not a boundary."""
    plan = _plan_two_phases()
    result = _detect_boundary(plan, "implement", "build-step", "completed")
    assert result == BoundaryKind.NONE


def test_none_when_step_not_in_active_at_all():
    """step_id not in active list at all — not a boundary."""
    plan = _plan_two_phases()
    result = _detect_boundary(plan, "implement", "unknown-step", "completed")
    assert result == BoundaryKind.NONE


# ---------------------------------------------------------------------------
# (c) PHASE when step_id is last in active AND phase is NOT last in workflow_plan
# ---------------------------------------------------------------------------

def test_phase_boundary_when_last_step_of_non_last_phase():
    """Last step of 'specify' (not the last phase) → BoundaryKind.PHASE."""
    plan = _plan_two_phases()
    result = _detect_boundary(plan, "specify", "specify-step", "completed")
    assert result == BoundaryKind.PHASE, (
        f"Expected PHASE, got {result}"
    )


# ---------------------------------------------------------------------------
# (d) FEATURE when step_id is last in active AND phase IS last in workflow_plan
# ---------------------------------------------------------------------------

def test_feature_boundary_when_last_step_of_last_phase():
    """Last step of 'implement' (the last phase) → BoundaryKind.FEATURE."""
    plan = _plan_two_phases()
    result = _detect_boundary(plan, "implement", "test-step", "completed")
    assert result == BoundaryKind.FEATURE, (
        f"Expected FEATURE, got {result}"
    )


def test_feature_boundary_single_phase_plan():
    """Single-phase plan: only step of only phase → BoundaryKind.FEATURE."""
    plan = _plan_single_phase()
    result = _detect_boundary(plan, "implement", "only-step", "completed")
    assert result == BoundaryKind.FEATURE


# ---------------------------------------------------------------------------
# (e) NONE when workflow_plan has empty phase block (no 'active' key)
# ---------------------------------------------------------------------------

def test_none_when_phase_block_missing():
    """Phase key absent from workflow_plan → NONE (no boundary can be detected)."""
    plan = _plan_two_phases()
    result = _detect_boundary(plan, "missing-phase", "any-step", "completed")
    assert result == BoundaryKind.NONE


def test_none_when_active_is_none():
    """Phase block exists but active is None → NONE."""
    plan = {"implement": {"active": None, "filtered": []}}
    result = _detect_boundary(plan, "implement", "some-step", "completed")
    assert result == BoundaryKind.NONE


# ---------------------------------------------------------------------------
# (f) NONE when active list is empty
# ---------------------------------------------------------------------------

def test_none_when_active_is_empty_list():
    """Empty active list → NONE (no last element to compare against)."""
    plan = {"implement": {"active": [], "filtered": []}}
    result = _detect_boundary(plan, "implement", "some-step", "completed")
    assert result == BoundaryKind.NONE


# ---------------------------------------------------------------------------
# (g) Phase key order: feature boundary respects insertion order, not alpha order
# ---------------------------------------------------------------------------

def test_feature_boundary_uses_key_order_not_alphabetical():
    """'complete' sorts before 'implement' alphabetically, but 'implement' is last inserted."""
    plan = {
        "complete": {"active": ["wrap-up"], "filtered": []},
        "implement": {"active": ["final-step"], "filtered": []},
    }
    # 'complete' is not the last key; its last step should be PHASE, not FEATURE
    assert _detect_boundary(plan, "complete", "wrap-up", "completed") == BoundaryKind.PHASE
    # 'implement' IS the last key; its last step should be FEATURE
    assert _detect_boundary(plan, "implement", "final-step", "completed") == BoundaryKind.FEATURE


# ---------------------------------------------------------------------------
# BoundaryKind enum is importable and has expected values
# ---------------------------------------------------------------------------

def test_boundary_kind_values():
    """BoundaryKind enum has the three expected values."""
    assert BoundaryKind.NONE.value == "none"
    assert BoundaryKind.PHASE.value == "phase"
    assert BoundaryKind.FEATURE.value == "feature"
