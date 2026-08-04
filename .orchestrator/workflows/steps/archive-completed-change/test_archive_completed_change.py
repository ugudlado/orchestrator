"""Tests for config/steps/archive-completed-change/script.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent / "script.sh"


def _run_archive(
    slug: str, *, repo_root: Path, worktree_root: Path, archive_path: str
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["WORKTREE_ROOT"] = str(worktree_root)
    env["CHANGE_ID"] = slug
    env["ARCHIVE_PATH"] = archive_path
    env.pop("ORCHESTRATOR_WORKFLOW_DIR", None)
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )


@pytest.mark.xfail(
    reason="ORC-36 T-4: archive-completed-change.sh does not yet copy state.yaml into archive",
    strict=False,
)
def test_archive_contains_artifact_files(tmp_path):
    """archive-completed-change.sh must collect all worktree files into the archive dir."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    (repo / "README.md").write_text("test repo")

    slug = "demo-feature"
    src = worktree / "spec" / "changes" / slug
    src.mkdir(parents=True)
    (src / "state.yaml").write_text("change_id: demo-feature\nschema: feature\nstatus: completed\n")
    (src / "plan.yaml").write_text("phase: complete\n")
    (src / "tasks.md").write_text("- [x] T-1: done\n")
    (src / "design.md").write_text("# Design\n")

    archive_rel = f"spec/changes/archive/2026-05-03-{slug}"
    result = _run_archive(slug, repo_root=repo, worktree_root=worktree, archive_path=archive_rel)
    archive_dir = repo / archive_rel

    assert (archive_dir / "state.yaml").exists(), (
        f"archive missing state.yaml\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert (archive_dir / "tasks.md").exists(), (
        f"archive missing tasks.md\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert (archive_dir / "design.md").exists(), (
        f"archive missing design.md\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert not src.exists(), "worktree source dir should be removed after archive"
