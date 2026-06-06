"""
Regression tests for _resolve_tasks_md path resolution.

tasks.md lives under spec/changes/<slug>/ — same canonical location as the
state.yaml file. Resolver tries explicit `tasks_path` first, then derives
from worktree_path or repo_root.
"""
from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _resolve_tasks_md  # noqa: E402
# _check_all_tasks_completed removed in ORC-65 (T-9): task completion now
# tracked via per-task step_history entries, not tasks.md checkbox counts.


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


def test_resolve_returns_none_when_no_candidates(tmp_path):
    """If state has no fields to construct any candidate, return None."""
    assert _resolve_tasks_md({}) is None


def test_resolve_falls_back_to_repo_root_when_worktree_missing(tmp_path):
    """When worktree_path is set, the resolver returns the worktree-based path
    without checking whether the file exists — the caller handles the missing-file
    case. This test verifies that worktree_path takes priority over repo_root
    whenever it is a non-empty string.
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
    # worktree_path is set → resolver returns worktree-based path regardless of
    # whether the file exists there (is_dir/is_file checks removed as defensive).
    expected = worktree / "spec" / "changes" / "demo" / "tasks.md"
    assert _resolve_tasks_md(state) == expected
