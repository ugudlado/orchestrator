"""
Regression tests for _resolve_tasks_md path resolution.

Phase 4 retro item: the original lookup only tried `<worktree>/spec/changes/<change_id>/tasks.md`,
which fails to find tasks.md at the canonical workflow-engine location
`<repo_root>/.state/<change_id>/tasks.md`. With the bug, _check_all_tasks_completed
fail-opens (returns True) and the dispatcher advances to phase review with
unchecked tasks.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _resolve_tasks_md, _check_all_tasks_completed  # noqa: E402


def test_resolve_finds_state_dir_tasks_md(tmp_path):
    """The .state/<slug>/tasks.md location must be found when it exists."""
    repo = tmp_path / "repo"
    state_tasks = repo / ".state" / "demo" / "tasks.md"
    state_tasks.parent.mkdir(parents=True)
    state_tasks.write_text("- [ ] T-1: pending\n")

    state = {"repo_root": str(repo), "change_id": "demo"}
    resolved = _resolve_tasks_md(state)
    assert resolved == state_tasks


def test_resolve_prefers_explicit_tasks_path(tmp_path):
    """When tasks_path is set explicitly, it wins over fallbacks."""
    explicit = tmp_path / "custom" / "tasks.md"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("- [ ] T-1: pending\n")

    repo = tmp_path / "repo"
    (repo / ".state" / "demo").mkdir(parents=True)
    (repo / ".state" / "demo" / "tasks.md").write_text("- [x] T-1: done\n")

    state = {"tasks_path": str(explicit), "repo_root": str(repo), "change_id": "demo"}
    resolved = _resolve_tasks_md(state)
    assert resolved == explicit


def test_resolve_falls_back_to_worktree_when_state_missing(tmp_path):
    """If neither explicit nor .state location exists, the worktree path is returned."""
    worktree = tmp_path / "wt"
    worktree_tasks = worktree / "spec" / "changes" / "demo" / "tasks.md"
    worktree_tasks.parent.mkdir(parents=True)
    worktree_tasks.write_text("- [ ] T-1: pending\n")

    state = {"worktree_path": str(worktree), "change_id": "demo"}
    resolved = _resolve_tasks_md(state)
    assert resolved == worktree_tasks


def test_check_all_tasks_completed_reads_state_dir_tasks_md(tmp_path):
    """The fail-open bug: _check_all_tasks_completed previously returned True when
    tasks.md was at .state/<slug>/tasks.md. Now it correctly finds and reads it."""
    repo = tmp_path / "repo"
    state_tasks = repo / ".state" / "demo" / "tasks.md"
    state_tasks.parent.mkdir(parents=True)
    state_tasks.write_text("- [ ] T-1: pending\n- [x] T-2: done\n")

    state = {"repo_root": str(repo), "change_id": "demo"}
    assert _check_all_tasks_completed(state) is False, (
        "should detect the unchecked T-1 in .state/demo/tasks.md"
    )


def test_check_all_tasks_completed_returns_true_when_all_checked(tmp_path):
    """Sanity: when all tasks are [x], the predicate returns True."""
    repo = tmp_path / "repo"
    state_tasks = repo / ".state" / "demo" / "tasks.md"
    state_tasks.parent.mkdir(parents=True)
    state_tasks.write_text("- [x] T-1: done\n- [x] T-2: done\n")

    state = {"repo_root": str(repo), "change_id": "demo"}
    assert _check_all_tasks_completed(state) is True


def test_resolve_returns_none_when_no_candidates(tmp_path):
    """If state has no fields to construct any candidate, return None."""
    state = {}
    assert _resolve_tasks_md(state) is None
