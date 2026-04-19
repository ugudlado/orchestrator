"""
Tests for archive-completed-change.sh backlog cleanup.

T-5 (RED): archive-completed-change.sh removes spec/changes/backlog/<CHANGE_ID>/
after archiving, via a separate cleanup commit.

Design spec: design.md §3
"""
from __future__ import annotations

import os
import subprocess

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "scripts",
        "inline",
        "archive-completed-change.sh",
    )
)


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        capture_output=True,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# Fixture: temp git repo with state + backlog
# ---------------------------------------------------------------------------

@pytest.fixture()
def archive_env(tmp_path):
    """
    Set up two directories:
      - repo/   — a git repo representing REPO_ROOT
      - state/  — workflow state dir (WORKFLOW_STATE_DIR)

    Inside repo:
      - spec/changes/archive/   (archive destination parent)
      - spec/changes/backlog/test-slug/backlog.md  (tracked & committed)

    Inside state:
      - test-slug/state.yaml  (source for archive script)

    Returns a dict of paths and env vars.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()

    # Init git repo
    _git(tmp_path, "init", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    # Initial commit so HEAD exists
    init_file = repo / ".gitkeep"
    init_file.write_text("init")
    _git(repo, "add", ".gitkeep")
    _git(repo, "commit", "-m", "chore: initial commit")

    # Create archive parent dir
    archive_parent = repo / "spec" / "changes" / "archive"
    archive_parent.mkdir(parents=True)

    # Create backlog dir with content and commit it
    backlog_dir = repo / "spec" / "changes" / "backlog" / "test-slug"
    backlog_dir.mkdir(parents=True)
    (backlog_dir / "backlog.md").write_text("# test-slug\n\nStub backlog entry.\n")
    _git(repo, "add", "spec/changes/backlog/test-slug")
    _git(repo, "commit", "-m", "chore: add test-slug backlog entry")

    # Create workflow state source
    state_dir = state_root / "test-slug"
    state_dir.mkdir(parents=True)
    (state_dir / "state.yaml").write_text(
        "change_id: test-slug\nstatus: completed\nphase: complete\n"
        "completed_at: 2026-04-19T00:00:00Z\n"
        "archive_path: spec/changes/archive/2026-04-19-test-slug\n"
    )

    archive_path = "spec/changes/archive/2026-04-19-test-slug"

    env = {
        **os.environ,
        "REPO_ROOT": str(repo),
        "WORKFLOW_STATE_DIR": str(state_root),
        "CHANGE_ID": "test-slug",
        "ARCHIVE_PATH": archive_path,
    }

    return {
        "repo": repo,
        "state_root": state_root,
        "backlog_dir": backlog_dir,
        "archive_path": archive_path,
        "env": env,
    }


# ---------------------------------------------------------------------------
# T-5: Backlog cleanup tests
# ---------------------------------------------------------------------------

class TestArchiveBacklogCleanup:

    def test_script_exits_zero(self, archive_env):
        """archive-completed-change.sh exits 0 when all inputs are valid."""
        result = subprocess.run(
            ["bash", SCRIPT_PATH],
            env=archive_env["env"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Script exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_backlog_dir_removed_after_archive(self, archive_env):
        """archive-completed-change.sh removes spec/changes/backlog/<CHANGE_ID>/ after archiving."""
        subprocess.run(
            ["bash", SCRIPT_PATH],
            env=archive_env["env"],
            capture_output=True,
            text=True,
        )
        assert not archive_env["backlog_dir"].exists(), (
            f"Backlog directory still exists: {archive_env['backlog_dir']}"
        )

    def test_cleanup_commit_in_git_log(self, archive_env):
        """A separate cleanup commit is created after the archive commit."""
        subprocess.run(
            ["bash", SCRIPT_PATH],
            env=archive_env["env"],
            capture_output=True,
            text=True,
        )
        log = _git(archive_env["repo"], "log", "--oneline")
        assert "cleanup:" in log.stdout, (
            f"No cleanup commit found in git log:\n{log.stdout}"
        )
