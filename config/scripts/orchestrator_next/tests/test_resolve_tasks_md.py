"""
Regression tests for _resolve_tasks_md path resolution.

tasks.md lives under spec/changes/<slug>/ — same canonical location as the
state.yaml file. Resolver tries explicit `tasks_path` first, then derives
from worktree_path or repo_root.
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


def test_resolve_finds_repo_root_tasks_md(tmp_path):
    """tasks.md under <repo_root>/spec/changes/<slug>/ is found."""
    repo = tmp_path / "repo"
    tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] T-1: pending\n")

    state = {"repo_root": str(repo), "change_id": "demo"}
    assert _resolve_tasks_md(state) == tasks


def test_resolve_prefers_explicit_tasks_path(tmp_path):
    """When tasks_path is set explicitly, it wins over fallbacks."""
    explicit = tmp_path / "custom" / "tasks.md"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("- [ ] T-1: pending\n")

    repo = tmp_path / "repo"
    fallback = repo / "spec" / "changes" / "demo" / "tasks.md"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("- [x] T-1: done\n")

    state = {"tasks_path": str(explicit), "repo_root": str(repo), "change_id": "demo"}
    assert _resolve_tasks_md(state) == explicit


def test_resolve_uses_worktree_when_present(tmp_path):
    """worktree_path takes precedence over repo_root for the spec/changes lookup."""
    worktree = tmp_path / "wt"
    tasks = worktree / "spec" / "changes" / "demo" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] T-1: pending\n")

    state = {"worktree_path": str(worktree), "change_id": "demo"}
    assert _resolve_tasks_md(state) == tasks


def test_check_all_tasks_completed_reads_tasks_md(tmp_path):
    """Predicate detects unchecked tasks under spec/changes/<slug>/."""
    repo = tmp_path / "repo"
    tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] T-1: pending\n- [x] T-2: done\n")

    state = {"repo_root": str(repo), "change_id": "demo"}
    assert _check_all_tasks_completed(state) is False


def test_check_all_tasks_completed_returns_true_when_all_checked(tmp_path):
    """Sanity: when all tasks are [x], the predicate returns True."""
    repo = tmp_path / "repo"
    tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [x] T-1: done\n- [x] T-2: done\n")

    state = {"repo_root": str(repo), "change_id": "demo"}
    assert _check_all_tasks_completed(state) is True


def test_resolve_returns_none_when_no_candidates(tmp_path):
    """If state has no fields to construct any candidate, return None."""
    assert _resolve_tasks_md({}) is None


def test_check_all_tasks_completed_fail_closed_when_path_missing(tmp_path):
    """When a candidate path is constructible but tasks.md does not exist,
    _check_all_tasks_completed must return False (fail-closed), not True.

    Regression test for record.py:931 — the fail-open `except FileNotFoundError:
    return True` branch that caused all tasks to be silently skipped in ORC-37.
    """
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    # Create the directories so a candidate path can be constructed,
    # but deliberately do NOT create tasks.md anywhere.
    worktree.mkdir()
    repo.mkdir()

    state = {
        "worktree_path": str(worktree),
        "repo_root": str(repo),
        "change_id": "demo",
    }
    # Current (buggy) code hits the except-FileNotFoundError branch and returns True.
    # Fixed code should return False when a candidate path exists but the file is missing.
    assert _check_all_tasks_completed(state) is False


def test_resolve_falls_back_to_repo_root_when_worktree_missing(tmp_path):
    """When worktree_path is set but that directory does not exist,
    _resolve_tasks_md should return the repo_root-based candidate path.

    Regression test for record.py:906 — `worktree_path or repo_root` picks
    worktree_path purely by string truthiness, ignoring whether the directory
    exists. After the fix, a missing worktree directory causes the resolver
    to fall back to repo_root.
    """
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    # Create repo tasks.md but NOT the worktree directory.
    repo_tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    repo_tasks.parent.mkdir(parents=True)
    repo_tasks.write_text("- [ ] T-1: pending\n")

    state = {
        "worktree_path": str(worktree),  # directory does not exist on disk
        "repo_root": str(repo),
        "change_id": "demo",
    }
    # Current (buggy) code: root = worktree_path (string truthy), builds worktree
    # candidate, finds no existing file, returns candidates[-1] = worktree path.
    # Fixed code: detects worktree dir is absent, uses repo_root candidate instead.
    assert _resolve_tasks_md(state) == repo_tasks
