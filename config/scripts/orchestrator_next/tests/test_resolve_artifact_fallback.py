"""
Regression tests for _resolve_workflow_artifact_path / _check_all_tasks_completed —
Bug 1: resolver fall-through when worktree dir exists but tasks.md is absent there.

Root cause (record.py, priority-2 branch): the resolver picks
  <worktree>/spec/changes/<change_id>/tasks.md
whenever the worktree DIRECTORY exists on disk, regardless of whether tasks.md
actually lives there.  When it doesn't, `_check_all_tasks_completed` hits
FileNotFoundError → fail-closed → returns False → dispatch re-emits execute-next-task
indefinitely.

Fix: when the worktree-relative candidate is absent, fall through to priority 3
(repo_root).  These tests FAIL on the current (unfixed) code.
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


# ---------------------------------------------------------------------------
# Case 1 (FAILING on buggy code):
# Worktree dir exists on disk, tasks.md is absent in the worktree artifact dir,
# but tasks.md IS present at repo_root.
# Expected (fixed):  resolver returns the repo_root path.
# Buggy behaviour:   resolver returns the worktree path (wt.is_dir() → True),
#                    ignoring that tasks.md is not there.
# ---------------------------------------------------------------------------
def test_resolver_falls_through_to_repo_root_when_worktree_file_missing(tmp_path):
    """Worktree dir exists but tasks.md absent there → resolver must return repo_root path.

    On buggy code priority-2 returns the worktree candidate (dir exists) even
    though tasks.md is not present there, so the assertion fails with a path
    mismatch: buggy returns <worktree>/spec/changes/demo/tasks.md instead of
    <repo_root>/spec/changes/demo/tasks.md.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()  # dir exists — this is the trigger condition for the bug
    # tasks.md is NOT created inside the worktree artifact dir

    repo = tmp_path / "repo"
    repo_tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    repo_tasks.parent.mkdir(parents=True)
    repo_tasks.write_text("- [x] T-1: done\n")

    state = {
        "worktree_path": str(worktree),
        "repo_root": str(repo),
        "change_id": "demo",
    }
    # Bug: returns worktree path (wt.is_dir() passes, no is_file() guard).
    # Fix: returns repo_root path because worktree candidate is absent.
    assert _resolve_tasks_md(state) == repo_tasks, (
        "resolver must fall through to repo_root when worktree dir exists but "
        "tasks.md is absent at the worktree-relative path"
    )


# ---------------------------------------------------------------------------
# Case 2 (FAILING on buggy code):
# Same setup — worktree dir exists, tasks.md absent in worktree, all tasks
# CHECKED in repo_root tasks.md.
# Expected (fixed):  _check_all_tasks_completed returns True (loop should exit).
# Buggy behaviour:   resolver locks onto worktree path → read_text() raises
#                    FileNotFoundError → fail-closed → returns False.
# ---------------------------------------------------------------------------
def test_all_tasks_completed_true_when_worktree_file_missing_repo_all_checked(tmp_path):
    """Worktree dir exists, tasks.md absent there, repo_root has all-[x] → True.

    This is the ORC-45 infinite-loop scenario.  Buggy code returns False via the
    FileNotFoundError fail-closed branch because it resolved to a non-existent
    worktree path.  Fixed code reads the repo_root path and finds all tasks done.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()  # dir exists, no tasks.md inside

    repo = tmp_path / "repo"
    repo_tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    repo_tasks.parent.mkdir(parents=True)
    repo_tasks.write_text("- [x] T-1: done\n- [x] T-2: done\n")

    state = {
        "worktree_path": str(worktree),
        "repo_root": str(repo),
        "change_id": "demo",
    }
    # Bug: FileNotFoundError on worktree path → returns False → loop never exits.
    # Fix: falls through to repo_root, reads all-checked file → returns True.
    assert _check_all_tasks_completed(state) is True, (
        "_check_all_tasks_completed must return True when repo_root tasks.md has "
        "all items checked and the worktree-relative copy is absent"
    )


# ---------------------------------------------------------------------------
# Case 3 (FAILING on buggy code):
# Same setup — worktree dir exists, tasks.md absent in worktree, repo_root has
# UNCHECKED tasks.
# Expected (fixed):  returns False (there are unfinished tasks).
# Buggy behaviour:   also returns False, but via FileNotFoundError fail-closed
#                    rather than by actually reading the file.
# This case is included to confirm the behaviour is correct for both pending
# and all-done states after the fall-through fix.
#
# NOTE: This case passes on BOTH buggy and fixed code (both return False),
# so it serves as a regression guard, not as a primary failing case.
# ---------------------------------------------------------------------------
def test_all_tasks_completed_false_when_worktree_file_missing_repo_has_unchecked(tmp_path):
    """Worktree dir exists, tasks.md absent there, repo_root has unchecked tasks → False.

    Regression guard: fixed code should still return False (tasks are not done),
    just for the right reason (found unchecked items, not FileNotFoundError).
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()

    repo = tmp_path / "repo"
    repo_tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    repo_tasks.parent.mkdir(parents=True)
    repo_tasks.write_text("- [ ] T-1: pending\n- [x] T-2: done\n")

    state = {
        "worktree_path": str(worktree),
        "repo_root": str(repo),
        "change_id": "demo",
    }
    assert _check_all_tasks_completed(state) is False


# ---------------------------------------------------------------------------
# Case 4 (passes on both buggy and fixed code — regression guard):
# Worktree dir exists AND tasks.md is present at the worktree-relative path.
# Resolver should return the worktree path (priority 2 fires correctly).
# ---------------------------------------------------------------------------
def test_resolver_returns_worktree_path_when_file_present_there(tmp_path):
    """When tasks.md is actually present in the worktree artifact dir, return it.

    This is the normal worktree case — fixed code must not break it.
    Passes on both buggy and fixed code (regression guard).
    """
    worktree = tmp_path / "wt"
    wt_tasks = worktree / "spec" / "changes" / "demo" / "tasks.md"
    wt_tasks.parent.mkdir(parents=True)
    wt_tasks.write_text("- [x] T-1: done\n")

    repo = tmp_path / "repo"
    repo_tasks = repo / "spec" / "changes" / "demo" / "tasks.md"
    repo_tasks.parent.mkdir(parents=True)
    repo_tasks.write_text("- [ ] T-1: stale-pending\n")

    state = {
        "worktree_path": str(worktree),
        "repo_root": str(repo),
        "change_id": "demo",
    }
    assert _resolve_tasks_md(state) == wt_tasks, (
        "when tasks.md IS present at the worktree-relative path, resolver must "
        "return that path (priority 2 should still fire)"
    )
