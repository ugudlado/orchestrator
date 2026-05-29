"""
ORC-36 regression tests: path-split failure modes (T-1 — RED on HEAD).

Four sub-tests that assert the post-fix expectation for each failure mode
described in the orc-36 diagnosis artifact. All four FAIL on HEAD before T-2/T-3/T-4 land.
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
# Sub-test 1 (T-3 RED): _resolve_feature_metrics_tasks_path resolves to spec/changes/
# ---------------------------------------------------------------------------

def test_resolve_tasks_path_uses_spec_changes(tmp_path):
    """
    POST-FIX: _resolve_feature_metrics_tasks_path(state) returns
      <repo_root>/spec/changes/<slug>/tasks.md

    PRE-FIX (HEAD): returns <repo_root>/.state/<slug>/tasks.md — FAILS here.

    orc-36 failure mode #1 / record.py line 798.
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

    orc-36 failure mode #2 / record.py lines 822-826.
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
# Sub-test 4 (T-2 RED): seed-state.sh writes to spec/changes/<slug>/
# ---------------------------------------------------------------------------

def test_seed_state_writes_to_spec_changes(tmp_path):
    """
    POST-FIX: seed-state.sh writes state.yaml and plan.yaml to
      spec/changes/<slug>/state.yaml  (WORKFLOW_STATE_DIR default = spec/changes)
      and does NOT create a .state/ directory.

    PRE-FIX (HEAD): writes to .state/<slug>/state.yaml — FAILS here.

    orc-36 failure mode #4 / seed-state.sh line 49.
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
