"""
Tests for archive-completed-change.sh backlog cleanup.

archive-completed-change.sh removes the H2 section keyed by CHANGE_ID from
spec/changes/backlog.md after archiving, via a separate cleanup commit.
The backlog migrated from a per-slug directory layout to a single file
with one H2 per slug; these tests reflect the current layout.
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
      - spec/changes/archive/  (archive destination parent)
      - spec/changes/backlog.md  (tracked & committed; contains an H2 for test-slug
        plus a sibling H2 to prove only the matching section is stripped)

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

    # Create the consolidated backlog file with two entries; only test-slug
    # should be removed by the archive script.
    backlog_file = repo / "spec" / "changes" / "backlog.md"
    backlog_file.parent.mkdir(parents=True, exist_ok=True)
    backlog_file.write_text(
        "# Backlog\n\n"
        "## test-slug\n\n"
        "Stub backlog entry for the slug under test.\n\n"
        "---\n\n"
        "## other-slug\n\n"
        "Sibling entry that must survive cleanup.\n"
    )
    _git(repo, "add", "spec/changes/backlog.md")
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
        "backlog_file": backlog_file,
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

    def test_backlog_section_removed_after_archive(self, archive_env):
        """archive-completed-change.sh strips the CHANGE_ID H2 from backlog.md and leaves siblings intact."""
        subprocess.run(
            ["bash", SCRIPT_PATH],
            env=archive_env["env"],
            capture_output=True,
            text=True,
        )
        text = archive_env["backlog_file"].read_text()
        assert "## test-slug" not in text, (
            f"test-slug section still present in backlog.md:\n{text}"
        )
        assert "## other-slug" in text, (
            f"sibling other-slug section was incorrectly removed:\n{text}"
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
