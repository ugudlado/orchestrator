"""
ORC-36 regression tests: path-split failure modes (T-1 — RED on HEAD).

Four sub-tests that assert the post-fix expectation for each failure mode
described in diagnose.md. All four FAIL on HEAD before T-2/T-3/T-4 land.
They PASS once the corresponding fix task ships.

Failure mode map:
  test_resolve_tasks_path_uses_spec_changes    → T-3 fix (record.py line 798)
  test_resolve_feature_metrics_no_raise        → T-3 fix (same function)
  test_archive_contains_artifact_files         → T-4 fix (archive-completed-change.sh)
  test_seed_state_writes_to_spec_changes       → T-2 fix (seed-state.sh line 49)
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

_HERE = Path(__file__).parent.resolve()
# parents[0] = orchestrator_next, [1] = scripts, [2] = config, [3] = repo root
_REPO_ROOT = _HERE.parents[3]
_SCRIPTS_DIR = str(_HERE.parents[1])  # config/scripts/
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_SEED_SCRIPT = _REPO_ROOT / "skills" / "orchestrate" / "scripts" / "seed-state.sh"
_ARCHIVE_SCRIPT = _REPO_ROOT / "config" / "scripts" / "inline" / "archive-completed-change.sh"
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


def _run_seed(slug: str, schema: str, *, repo_root: Path,
              flag_overrides: list[str] | None = None,
              extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run seed-state.sh with NO explicit WORKFLOW_STATE_DIR so the default fires."""
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["ORCHESTRATOR_HOME"] = _ORCHESTRATOR_HOME
    # Remove any inherited WORKFLOW_STATE_DIR so the script's default is exercised.
    env.pop("WORKFLOW_STATE_DIR", None)
    # Ensure orchestrator_next is importable in the subprocess.
    real_scripts_dir = str(_HERE.parents[1])
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{real_scripts_dir}:{existing_pypath}" if existing_pypath else real_scripts_dir
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_SEED_SCRIPT), slug, schema, *(flag_overrides or [])],
        capture_output=True,
        text=True,
        env=env,
    )


def _run_archive(
    slug: str, *, repo_root: Path, workflow_state_dir: Path, archive_path: str
) -> subprocess.CompletedProcess:
    """Run archive-completed-change.sh with explicit env vars."""
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["WORKFLOW_STATE_DIR"] = str(workflow_state_dir)
    env["CHANGE_ID"] = slug
    env["ARCHIVE_PATH"] = archive_path
    return subprocess.run(
        ["bash", str(_ARCHIVE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )


# ---------------------------------------------------------------------------
# Sub-test 1 (T-3 RED): _resolve_feature_metrics_tasks_path resolves to spec/changes/
# ---------------------------------------------------------------------------

def test_resolve_tasks_path_uses_spec_changes(tmp_path):
    """
    POST-FIX: _resolve_feature_metrics_tasks_path(state) returns
      <repo_root>/spec/changes/<slug>/tasks.md

    PRE-FIX (HEAD): returns <repo_root>/.state/<slug>/tasks.md — FAILS here.

    diagnose.md failure mode #1 / record.py line 798.
    """
    from orchestrator_next.record import _resolve_feature_metrics_tasks_path

    repo = tmp_path / "repo"
    repo.mkdir()

    state = {
        "repo_root": str(repo),
        "change_id": "demo-feature",
        # Deliberately omit tasks_path — exercises the fallback branch at line 798.
    }

    resolved = _resolve_feature_metrics_tasks_path(state)

    expected = repo / "spec" / "changes" / "demo-feature" / "tasks.md"
    assert resolved == expected, (
        f"[ORC-36 failure mode 1] _resolve_feature_metrics_tasks_path returned:\n"
        f"  {resolved}\n"
        f"Expected (post-fix):\n"
        f"  {expected}\n"
        f"Pre-fix behaviour: returns <repo>/.state/demo-feature/tasks.md (wrong location)."
    )


# ---------------------------------------------------------------------------
# Sub-test 2 (T-3 RED): _resolve_feature_metrics does NOT raise when tasks.md
#                        is in spec/changes/<slug>/ (no .state copy, no tasks_path)
# ---------------------------------------------------------------------------

def test_resolve_feature_metrics_no_raise(tmp_path):
    """
    POST-FIX: _resolve_feature_metrics(state, change_id) does NOT raise
      FileNotFoundError when tasks.md exists only at spec/changes/<slug>/tasks.md.

    PRE-FIX (HEAD): raises FileNotFoundError because the resolver looks in
      .state/<slug>/tasks.md and finds nothing — FAILS here.

    diagnose.md failure mode #2 / record.py lines 822-826.
    """
    from orchestrator_next.record import _resolve_feature_metrics

    repo = tmp_path / "repo"
    spec_dir = repo / "spec" / "changes" / "demo-feature"
    spec_dir.mkdir(parents=True)

    # Write tasks.md ONLY in spec/changes — no .state/ copy, no tasks_path override.
    (spec_dir / "tasks.md").write_text(
        "- [x] T-1: Write regression test\n"
        "- [ ] T-2: Apply the fix\n"
    )

    state = {
        "change_id": "demo-feature",
        "schema": "bugfix",
        "repo_root": str(repo),
        "worktree_path": str(repo),
        "started_at": "2026-05-03T00:00:00Z",
        "completed_at": "2026-05-03T01:00:00Z",
        "step_history": [],
        # No tasks_path key — exercises the fallback path.
    }

    # Post-fix: must not raise.
    try:
        result = _resolve_feature_metrics(state, "demo-feature")
    except FileNotFoundError as exc:
        pytest.fail(
            f"[ORC-36 failure mode 2] _resolve_feature_metrics raised FileNotFoundError:\n"
            f"  {exc}\n"
            f"Post-fix expectation: resolves tasks.md from spec/changes/<slug>/ and "
            f"returns metrics dict without raising."
        )

    # Sanity: the returned dict should reflect the real task counts.
    assert result.get("tasks_total") == 2, (
        f"Expected tasks_total=2 (post-fix reads actual tasks.md), got: {result.get('tasks_total')}"
    )


# ---------------------------------------------------------------------------
# Sub-test 3 (T-4 RED): archive-completed-change.sh preserves artifact files
# ---------------------------------------------------------------------------

def test_archive_contains_artifact_files(tmp_path):
    """
    POST-FIX: when WORKFLOW_STATE_DIR defaults to spec/changes (the new default),
      archive-completed-change.sh moves spec/changes/<slug>/ atomically, so the
      resulting archive contains BOTH state.yaml AND artifact files (tasks.md, spec.md).

    PRE-FIX (HEAD): the script does cp -R from .state/<slug>/ which never contains
      artifact files — tasks.md and spec.md are absent from archive — FAILS here.

    We reproduce the pre-fix dual-location reality:
      - state.yaml is in .state/<slug>/ (where seed-state.sh puts it today)
      - tasks.md + spec.md are in spec/changes/<slug>/ (where agents write them)
    We then run archive-completed-change.sh with WORKFLOW_STATE_DIR=.state (pre-fix)
    and assert the archive DOES contain tasks.md — which fails because cp -R only
    copies .state/<slug>/, not the artifact files.

    diagnose.md failure mode #3 / archive-completed-change.sh lines 21, 30.
    """
    # Set up a minimal git repo so the script's `git add/commit` can run.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@orc36.test"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "ORC-36 Test"],
        capture_output=True, check=True,
    )
    # Git needs at least one commit so `git rev-parse HEAD` doesn't fail.
    (repo / "README.md").write_text("test repo")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        capture_output=True, check=True,
    )

    slug = "demo-feature"

    # Pre-fix reality: state.yaml is in .state/<slug>/ (seed-state.sh today).
    state_dir = repo / ".state" / slug
    state_dir.mkdir(parents=True)
    (state_dir / "state.yaml").write_text(
        "change_id: demo-feature\nschema: bugfix\nstatus: completed\n"
    )

    # Pre-fix reality: artifact files are in spec/changes/<slug>/ (agents write there).
    spec_dir = repo / "spec" / "changes" / slug
    spec_dir.mkdir(parents=True)
    (spec_dir / "tasks.md").write_text("- [x] T-1: done\n- [x] T-2: done\n")
    (spec_dir / "spec.md").write_text("# Spec\nThis is the spec.\n")

    # Stage and commit the spec/changes artifacts so git is happy.
    subprocess.run(
        ["git", "-C", str(repo), "add", "spec/"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "add spec artifacts"],
        capture_output=True, check=True,
    )

    archive_rel = f"spec/changes/archive/2026-05-03-{slug}"

    # Run archive with the PRE-FIX WORKFLOW_STATE_DIR (.state/ — what HEAD uses).
    result = _run_archive(
        slug,
        repo_root=repo,
        workflow_state_dir=repo / ".state",
        archive_path=archive_rel,
    )

    archive_dir = repo / archive_rel

    # Post-fix expectation: tasks.md and spec.md must be in the archive.
    # Pre-fix: cp -R from .state/<slug>/ only copies state.yaml; tasks.md absent.
    tasks_in_archive = (archive_dir / "tasks.md").exists()
    spec_in_archive = (archive_dir / "spec.md").exists()

    assert tasks_in_archive, (
        f"[ORC-36 failure mode 3] archive at {archive_dir} is missing tasks.md.\n"
        f"Archive script output:\n  stdout={result.stdout!r}\n  stderr={result.stderr!r}\n"
        f"Archive contents: {sorted(p.name for p in archive_dir.iterdir()) if archive_dir.exists() else '(dir missing)'}\n"
        f"Pre-fix: cp -R .state/<slug>/ only copies state.yaml; artifact files never included."
    )
    assert spec_in_archive, (
        f"[ORC-36 failure mode 3] archive at {archive_dir} is missing spec.md.\n"
        f"Archive contents: {sorted(p.name for p in archive_dir.iterdir()) if archive_dir.exists() else '(dir missing)'}"
    )


# ---------------------------------------------------------------------------
# Sub-test 4 (T-2 RED): seed-state.sh writes to spec/changes/<slug>/
# ---------------------------------------------------------------------------

def test_seed_state_writes_to_spec_changes(tmp_path):
    """
    POST-FIX: seed-state.sh writes state.yaml and plan.yaml to
      spec/changes/<slug>/state.yaml  (WORKFLOW_STATE_DIR default = spec/changes)
      and does NOT create a .state/ directory.

    PRE-FIX (HEAD): writes to .state/<slug>/state.yaml — FAILS here.

    diagnose.md failure mode #4 / seed-state.sh line 49.
    """
    assert _SEED_SCRIPT.exists(), (
        f"seed-state.sh not found at {_SEED_SCRIPT}. "
        "The script must exist before this test can run."
    )

    slug = "orc36-path-test"
    schema = "bugfix"
    fake_repo = tmp_path / "repo"
    _write_project_yaml(fake_repo)

    # Run seed-state.sh with no WORKFLOW_STATE_DIR — exercises the default.
    result = _run_seed(
        slug,
        schema,
        repo_root=fake_repo,
        flag_overrides=["worktree=false"],
    )
    assert result.returncode == 0, (
        f"seed-state.sh exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Post-fix expectation: state.yaml written to spec/changes/<slug>/.
    expected_state = fake_repo / "spec" / "changes" / slug / "state.yaml"
    assert expected_state.exists(), (
        f"[ORC-36 failure mode 4] state.yaml not found at post-fix path:\n"
        f"  {expected_state}\n"
        f"Pre-fix: seed-state.sh writes to .state/{slug}/state.yaml instead.\n"
        f"seed-state.sh stdout: {result.stdout!r}\n"
        f"seed-state.sh stderr: {result.stderr!r}"
    )

    # Post-fix expectation: no .state/ directory should have been created.
    old_state_dir = fake_repo / ".state"
    assert not old_state_dir.exists(), (
        f"[ORC-36 failure mode 4] .state/ directory was created at {old_state_dir}.\n"
        f"Post-fix: seed-state.sh must NOT create .state/. "
        f"Only spec/changes/{slug}/ should exist."
    )
