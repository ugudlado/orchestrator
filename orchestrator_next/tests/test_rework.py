"""Tests for rework workflow config and run_rework."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrator_next.rework import rework_step_ids  # noqa: E402


def test_rework_workflow_step_list(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    assert rework_step_ids() == ["ticket-rework"]


def test_run_rework_worktree_archive(tmp_path, monkeypatch):
    """Mirror test_qa_rework_worktree: archived state under worktree archive path."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)

    test_repo = tmp_path / "repo"
    wt_base = tmp_path / "wt" / "orc-fixture"
    stub_bin = tmp_path / "stubs"
    stub_bin.mkdir()
    backlog_log = tmp_path / "backlog.log"

    (stub_bin / "backlog").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{backlog_log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (stub_bin / "backlog").chmod(0o755)

    test_repo.mkdir()
    (test_repo / "spec").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=test_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=test_repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=test_repo,
        check=True,
    )
    (test_repo / "spec" / "project.yaml").write_text(
        "version: 1\nticketing: backlog\n", encoding="utf-8"
    )
    (test_repo / "README.md").write_text("readme\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=test_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=test_repo, check=True)
    subprocess.run(["git", "branch", "-m", "main"], cwd=test_repo, check=True)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "feature/orc-fixture"],
        cwd=test_repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=test_repo, check=True)
    wt_base.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt_base), "feature/orc-fixture"],
        cwd=test_repo,
        check=True,
    )

    archive_dir = wt_base / "spec" / "changes" / "archive" / "orc-fixture"
    archive_dir.mkdir(parents=True)
    state_path = archive_dir / "state.yaml"
    state_path.write_text(
        yaml.safe_dump(
            {
                "change_id": "orc-fixture",
                "schema": "feature",
                "status": "completed",
                "repo_root": str(test_repo),
                "worktree_path": str(wt_base),
                "branch": "feature/orc-fixture",
                "ticket_id": "task-99",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "ORCHESTRATOR_HOME": _REPO_ROOT,
        "WORKTREE_ROOT": str(wt_base),
        "HOME": str(home),
    }

    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator_next.rework", str(state_path)],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    log = backlog_log.read_text(encoding="utf-8")
    assert "task edit task-99" in log
    assert "In Progress" in log
