"""Finalizing state.yaml when a run completes (originally ORC-124).

`orchestrator step` is the only verb that sees a run finish, so it owns the
transition: status == "completed", next_step cleared, scratch discarded. A
blocked run must be left alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from orchestrator_next.protocol import step  # noqa: E402


def _completed_state(tmp_path: Path) -> Path:
    """Seed a state.yaml whose workflow_plan node is already completed → exit 1."""
    sd = tmp_path / "repo" / ".orchestrator" / "fin"
    sd.mkdir(parents=True)
    sy = sd / "20260101T000000_feature_state.yaml"
    sy.write_text(yaml.safe_dump({
        "change_id": "fin", "schema": "feature", "version": 1,
        "status": "blocked",
        "next_step": {"phase": "main", "step_id": "foo"},
        "phase": "main",
        "repo_root": str(tmp_path / "repo"),
        "worktree_path": str(tmp_path / "repo"),
        "workflow_plan": {"main": {"nodes": [
            {"id": "foo", "status": "completed"},
        ]}},
        "step_history": [
            {"step_id": "foo", "phase": "main", "status": "completed",
             "agent": "developer", "attempt": 1,
             "started_at": "2026-01-01T00:00:00Z",
             "ended_at": "2026-01-01T00:00:01Z"},
        ],
    }))
    return sy


def _blocked_state(tmp_path: Path) -> Path:
    """Seed a state.yaml whose last step_history entry is blocked → exit 2."""
    sd = tmp_path / "repo2" / ".orchestrator" / "blk"
    sd.mkdir(parents=True)
    sy = sd / "20260101T000000_feature_state.yaml"
    sy.write_text(yaml.safe_dump({
        "change_id": "blk", "schema": "feature", "version": 1,
        "status": "active",
        "next_step": None,
        "phase": "main",
        "repo_root": str(tmp_path / "repo2"),
        "worktree_path": str(tmp_path / "repo2"),
        "workflow_plan": {"main": {"nodes": [
            {"id": "bar", "status": "pending"},
        ]}},
        "step_history": [
            {"step_id": "bar", "phase": "main", "status": "blocked",
             "agent": "developer", "attempt": 1,
             "started_at": "2026-01-01T00:00:00Z",
             "ended_at": "2026-01-01T00:00:01Z"},
        ],
    }))
    return sy


def test_finalize_sets_status_completed_and_clears_next_step(tmp_path, monkeypatch):
    """AC-1, AC-2: completed run writes status=completed and next_step=None."""
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    sy = _completed_state(tmp_path)
    result, _ = step(str(sy))
    assert result["status"] == "done"
    raw = yaml.safe_load(sy.read_text())
    assert raw["status"] == "completed"
    assert raw["next_step"] is None


def test_finalize_preserves_step_history(tmp_path, monkeypatch):
    """Completed run must not drop step_history or other top-level keys."""
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    sy = _completed_state(tmp_path)
    step(str(sy))
    raw = yaml.safe_load(sy.read_text())
    assert len(raw.get("step_history", [])) == 1
    assert raw["change_id"] == "fin"
    assert raw["schema"] == "feature"


def test_blocked_exit_does_not_finalize(tmp_path, monkeypatch):
    """AC-3: blocked exit (code 2) must leave status == 'blocked', not 'completed'."""
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_NOTIFY_CMD", raising=False)
    sy = _blocked_state(tmp_path)
    result, _ = step(str(sy))
    assert result["status"] == "blocked"
    raw = yaml.safe_load(sy.read_text())
    assert raw["status"] != "completed"
