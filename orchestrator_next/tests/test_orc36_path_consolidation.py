"""
ORC-36 regression tests: archive and seed-state path correctness.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path constants (mirrors test_seed_state.py convention)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent.resolve()  # orchestrator_next/tests
# ORC-106: package at repo root. parents[0] = orchestrator_next, [1] = repo root.
_REPO_ROOT = _HERE.parents[1]
_SCRIPTS_DIR = str(_REPO_ROOT)  # package import path (repo root)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_ARCHIVE_SCRIPT = _REPO_ROOT / "config" / "steps" / "archive-completed-change" / "script.sh"
_ORCHESTRATOR_HOME = os.environ.get(
    "ORCHESTRATOR_HOME", str(Path.home() / ".config" / "orchestrator")
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_project_yaml(repo_root: Path) -> None:
    """Minimal spec/project.yaml so seed-state.sh passes the pre-condition check."""
    project = {
        "version": 1,
        "project": {
            "name": "orc36-test-repo",
            "repo": "orc36-test-repo",
            "summary": "Regression test repo for ORC-36",
        },
        "rules": [],
        "verify_commands": {"test": "pytest"},
    }
    (repo_root / "spec").mkdir(parents=True, exist_ok=True)
    (repo_root / "spec" / "project.yaml").write_text(
        yaml.safe_dump(project, sort_keys=False)
    )


def _run_seed(slug: str, schema: str, *, repo_root: Path, worktree_base: Path,
              fake_home: Path | None = None,
              flag_overrides: list[str] | None = None,
              extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run seed-state.sh against a real git repo with an isolated worktree base.

    ORC-108: seed-state.sh always creates a worktree, so WORKTREE_BASE_DIR is
    pinned to a tmp path (never the developer's real worktree directory).

    fake_home overrides HOME so state.yaml writes land under tmp_path instead
    of the real ~/.config/orchestrator.
    """
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["ORCHESTRATOR_HOME"] = _ORCHESTRATOR_HOME
    env["WORKTREE_BASE_DIR"] = str(worktree_base)
    if fake_home is not None:
        env["HOME"] = str(fake_home)
    # Ensure orchestrator_next is importable in the subprocess.
    real_scripts_dir = str(_HERE.parents[1])
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{real_scripts_dir}:{existing_pypath}" if existing_pypath else real_scripts_dir
    )
    if extra_env:
        env.update(extra_env)
    # Seeding ported to Python (run_loop._seed_state).
    real_scripts_dir = str(_HERE.parents[1])
    driver = (
        "import sys; sys.path.insert(0, {scripts!r});\n"
        "from orchestrator_next.run_loop import _seed_state;\n"
        "print(_seed_state({slug!r}, {schema!r}, {repo!r}, list({flags!r})))\n"
    ).format(scripts=real_scripts_dir, slug=slug, schema=schema,
             repo=str(repo_root), flags=flag_overrides or [])
    return subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True, text=True, env=env,
    )


def _run_archive(
    slug: str, *, repo_root: Path, worktree_root: Path, archive_path: str
) -> subprocess.CompletedProcess:
    """Run archive-completed-change.sh with explicit env vars."""
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["WORKTREE_ROOT"] = str(worktree_root)
    env["CHANGE_ID"] = slug
    env["ARCHIVE_PATH"] = archive_path
    env.pop("ORCHESTRATOR_WORKFLOW_DIR", None)
    return subprocess.run(
        ["bash", str(_ARCHIVE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )


# ---------------------------------------------------------------------------
# archive-completed-change.sh preserves artifact files
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="ORC-36 T-4: archive-completed-change.sh does not yet copy state.yaml into archive",
    strict=False,
)
def test_archive_contains_artifact_files(tmp_path):
    """
    All workflow files (state.yaml, plan.yaml, artifacts) live under the worktree
    at WORKTREE_ROOT/spec/changes/<slug>/. archive-completed-change.sh must collect
    everything from that single source into the archive destination.
    """
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    (repo / "README.md").write_text("test repo")

    slug = "demo-feature"

    # All files live in the worktree under spec/changes/<slug>/
    src = worktree / "spec" / "changes" / slug
    src.mkdir(parents=True)
    (src / "state.yaml").write_text("change_id: demo-feature\nschema: feature\nstatus: completed\n")
    (src / "plan.yaml").write_text("phase: complete\n")
    (src / "tasks.md").write_text("- [x] T-1: done\n")
    (src / "design.md").write_text("# Design\n")

    archive_rel = f"spec/changes/archive/2026-05-03-{slug}"

    result = _run_archive(
        slug,
        repo_root=repo,
        worktree_root=worktree,
        archive_path=archive_rel,
    )

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


# ---------------------------------------------------------------------------
# Sub-test 4: seed-state.sh writes state to ~/.config/orchestrator/<repo>/<slug>/
# ---------------------------------------------------------------------------

def test_seed_state_writes_to_spec_changes(tmp_path):
    """
    seed-state.sh writes state.yaml under $REPO_ROOT/.orchestrator/<slug>/,
    independent of the worktree. Artifacts still live in the worktree under
    spec/changes/<slug>/. No .state/ directory is created anywhere.

    orc-36 failure mode #4 (path shape) + canonical state location (ORC-114).
    """

    slug = "orc36-path-test"
    schema = "bugfix"
    fake_repo = tmp_path / "repo"
    worktree_base = tmp_path / "wt"
    fake_home = tmp_path / "home"
    fake_repo.mkdir(parents=True)
    subprocess.run(["git", "-C", str(fake_repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(fake_repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(fake_repo), "config", "user.name", "t"], check=True)
    _write_project_yaml(fake_repo)
    subprocess.run(["git", "-C", str(fake_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(fake_repo), "commit", "-qm", "init"], check=True)

    result = _run_seed(
        slug,
        schema,
        repo_root=fake_repo,
        worktree_base=worktree_base,
        fake_home=fake_home,
    )
    assert result.returncode == 0, (
        f"seed-state.sh exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # State written to $REPO_ROOT/.orchestrator/<slug>/<ts>_<schema>_state.yaml (ORC-114).
    state_dir = fake_repo / ".orchestrator" / slug
    matches = sorted(state_dir.glob(f"*_{schema}_state.yaml"))
    assert matches, (
        f"no *_{schema}_state.yaml found under {state_dir}\n"
        f"seed-state.sh stdout: {result.stdout!r}\n"
        f"seed-state.sh stderr: {result.stderr!r}"
    )

    # ORC-36 invariant preserved: no .state/ directory anywhere.
    assert not (fake_repo / ".state").exists(), "seed-state.sh must NOT create .state/ in the repo"
    assert not (worktree_base / slug / ".state").exists(), "seed-state.sh must NOT create .state/ in the worktree"
