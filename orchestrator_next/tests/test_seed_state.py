"""
Regression tests for skills/orchestrate/scripts/seed-state.sh (ORC-27).

Three scenarios per spec.md § Test Strategy:
  test_seed_state_produces_dispatch_ready_pair — RED until T-2 (seed-state.sh doesn't exist yet)
  test_seed_state_is_idempotent               — idempotent re-run leaves state.yaml unchanged
  test_seed_state_fails_without_project_yaml  — fail-loud when spec/project.yaml missing

The primary test (test_seed_state_produces_dispatch_ready_pair) fails before T-2 because it
asserts the script exists at skills/orchestrate/scripts/seed-state.sh. The failure cites the
missing script path, not an import error.

Fixture strategy:
  - ORCHESTRATOR_HOME points at the real ~/.config/orchestrator so generate_plan can load
    real schema YAMLs and step contracts (no copy overhead).
  - WORKFLOW_STATE_DIR and REPO_ROOT are isolated under tmp_path so no live state is touched.
  - spec/project.yaml is written into the tmp REPO_ROOT for the happy-path and idempotency
    tests; omitted for the fail-loud test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent.resolve()
# .../config/scripts/orchestrator_next/tests
# parents[0] = orchestrator_next
# parents[1] = scripts
# parents[2] = config
# parents[3] = repo root (worktree)
_REPO_ROOT = _HERE.parents[1]
_SEED_SCRIPT = _REPO_ROOT / "skills" / "orchestrate" / "scripts" / "seed-state.sh"
_ORCHESTRATOR_HOME = os.environ.get("ORCHESTRATOR_HOME", str(Path.home() / ".config" / "orchestrator"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project_yaml(repo_root: Path) -> None:
    """Write a minimal spec/project.yaml into the fake repo root."""
    project = {
        "version": 1,
        "project": {
            "name": "test-repo",
            "repo": "test-repo",
            "summary": "Integration test project",
        },
        "rules": [],
        "verify_commands": {"test": "pytest"},
    }
    spec_dir = repo_root / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "project.yaml").write_text(yaml.safe_dump(project, sort_keys=False))


def _init_git_repo(repo_root: Path) -> None:
    """Make repo_root a committed git repo so `git worktree add` succeeds.

    ORC-108: seed-state.sh creates a worktree unconditionally, which requires a
    real repo with at least one commit.
    """
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.name", "t"], check=True)


def _commit_all(repo_root: Path) -> None:
    subprocess.run(["git", "-C", str(repo_root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-qm", "init"], check=True)


def _run_seed(
    slug: str,
    schema: str,
    *,
    repo_root: Path,
    worktree_base: Path,
    fake_home: Path | None = None,
    flag_overrides: list[str] | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run seed-state.sh with the given slug/schema and isolated env vars.

    PYTHONPATH is set to the real config/scripts directory so the subprocess
    can import orchestrator_next regardless of where repo_root (the fake test
    repo) points.  The script respects an existing PYTHONPATH by prepending.

    WORKTREE_BASE_DIR is pinned to a tmp path so the unconditional worktree
    creation (ORC-108) never touches the developer's real worktree directory.

    fake_home, when provided, overrides HOME so state.yaml writes land under
    tmp_path instead of the real ~/.config/orchestrator.
    """
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["WORKTREE_BASE_DIR"] = str(worktree_base)
    env["ORCHESTRATOR_HOME"] = _ORCHESTRATOR_HOME
    if fake_home is not None:
        env["HOME"] = str(fake_home)
    # Ensure orchestrator_next is importable inside the subprocess:
    # _HERE.parents[1] == config/scripts/ (parent of orchestrator_next package)
    real_scripts_dir = str(_HERE.parents[1])
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{real_scripts_dir}:{existing_pypath}" if existing_pypath else real_scripts_dir
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_SEED_SCRIPT), slug, schema, *(flag_overrides or [])],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# test_seed_state_produces_dispatch_ready_pair
# ---------------------------------------------------------------------------


def test_seed_state_produces_dispatch_ready_pair(tmp_path):
    """
    seed-state.sh exists, the seeder exits 0, and state.yaml is written with a
    promoted `workflow_plan.main.nodes` graph (ORC-63: plan.yaml eliminated —
    generate_plan promotes the seeded workflow_plan in place).

    The end-to-end `orchestrator next` dispatch check moved to test_dispatch.py
    once DAG-walk dispatch landed (ORC-63 T-13).
    """
    assert _SEED_SCRIPT.exists(), (
        f"seed-state.sh not found at {_SEED_SCRIPT}."
    )

    slug = "orc-27-test"
    schema = "bugfix"
    fake_repo = tmp_path / "repo"
    worktree_base = tmp_path / "wt"
    fake_home = tmp_path / "home"
    _init_git_repo(fake_repo)
    _write_project_yaml(fake_repo)
    _commit_all(fake_repo)

    result = _run_seed(
        slug,
        schema,
        repo_root=fake_repo,
        worktree_base=worktree_base,
        fake_home=fake_home,
    )
    assert result.returncode == 0, (
        f"seed-state.sh exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    # Worktree is still created for implementation artifacts.
    worktree_path = worktree_base / slug
    assert worktree_path.is_dir(), "seed-state.sh did not create the worktree"
    # State lives in $HOME/.config/orchestrator/<repo-name>/<slug>/<ts>_<schema>_state.yaml.
    # The fake repo has no remote, so repo-name falls back to basename of fake_repo.
    repo_name = fake_repo.name
    state_dir = fake_home / ".config" / "orchestrator" / repo_name / slug
    matches = sorted(state_dir.glob(f"*_{schema}_state.yaml"))
    assert matches, f"no *_{schema}_state.yaml found under {state_dir}"
    state_yaml_path = matches[-1]
    # ORC-63: no separate plan file — workflow_plan is promoted inside state file.
    assert not (state_yaml_path.parent / "plan.yaml").exists(), (
        "seed-state.sh should not produce a separate plan file (ORC-63)"
    )

    # Verify state.yaml round-trips through parser.load_state
    scripts_parent = str(_HERE.parents[2])  # config/scripts/
    if scripts_parent not in sys.path:
        sys.path.insert(0, scripts_parent)
    from orchestrator_next.parser import load_state  # noqa: E402 (import inside test)

    state = load_state(str(state_yaml_path))
    assert state.change_id, "state.yaml missing change_id"
    assert state.phase, "state.yaml missing phase"
    assert state.workflow_plan, "state.yaml missing workflow_plan"

    # ORC-63: workflow_plan.main is promoted to a non-empty `nodes` graph,
    # each node born `pending`. No `active` key remains.
    main_block = state.workflow_plan["main"]
    assert "active" not in main_block, "active key should be removed after promotion"
    nodes = main_block["nodes"]
    assert isinstance(nodes, list) and nodes, "workflow_plan.main.nodes must be a non-empty list"
    for node in nodes:
        assert isinstance(node, dict) and "id" in node, f"malformed node: {node!r}"
        assert node.get("status") == "pending", f"node {node.get('id')!r} not pending at init"

    # ORC-34 regression: both created_at and started_at must be present and equal
    state_raw = yaml.safe_load(state_yaml_path.read_text())
    assert "created_at" in state_raw, "state.yaml missing created_at"
    assert "started_at" in state_raw, "state.yaml missing started_at (ORC-34: seed-state.sh must write started_at)"
    assert state_raw["started_at"] == state_raw["created_at"], (
        f"started_at ({state_raw.get('started_at')!r}) != created_at ({state_raw.get('created_at')!r})"
    )
    assert state_raw.get("project_context_loaded") is True
    # ORC-108: worktree is unconditional — state records its path and branch.
    assert state_raw.get("worktree_path") == str(worktree_path)
    assert state_raw.get("branch") == f"{schema}/{slug}"


# ---------------------------------------------------------------------------
# test_seed_state_is_idempotent
# ---------------------------------------------------------------------------


def test_seed_state_is_idempotent(tmp_path):
    """
    Re-running seed-state.sh when state.yaml already exists exits 0 without
    overwriting the existing file (FR-3 / AC-4).
    """
    assert _SEED_SCRIPT.exists(), (
        f"seed-state.sh not found at {_SEED_SCRIPT}. "
        "T-2 must create the script before this test passes."
    )

    slug = "orc-27-idempotent"
    schema = "bugfix"
    fake_repo = tmp_path / "repo"
    worktree_base = tmp_path / "wt"
    fake_home = tmp_path / "home"
    _init_git_repo(fake_repo)
    _write_project_yaml(fake_repo)
    _commit_all(fake_repo)

    # First run — creates <ts>_<schema>_state.yaml
    r1 = _run_seed(
        slug,
        schema,
        repo_root=fake_repo,
        worktree_base=worktree_base,
        fake_home=fake_home,
    )
    assert r1.returncode == 0, f"First seed failed: {r1.stderr}"

    repo_name = fake_repo.name
    state_dir = fake_home / ".config" / "orchestrator" / repo_name / slug
    matches = sorted(state_dir.glob(f"*_{schema}_state.yaml"))
    assert matches, f"no *_{schema}_state.yaml found after first seed"
    state_yaml_path = matches[-1]
    content_before = state_yaml_path.read_text()
    mtime_before = state_yaml_path.stat().st_mtime
    count_before = len(matches)

    # Second run — schema state already exists, must not create a new file
    r2 = _run_seed(
        slug,
        schema,
        repo_root=fake_repo,
        worktree_base=worktree_base,
        fake_home=fake_home,
    )
    assert r2.returncode == 0, f"Second (idempotent) seed failed: {r2.stderr}"

    matches_after = sorted(state_dir.glob(f"*_{schema}_state.yaml"))
    assert len(matches_after) == count_before, (
        f"idempotent run created a new state file (count {count_before} → {len(matches_after)})"
    )
    content_after = state_yaml_path.read_text()
    mtime_after = state_yaml_path.stat().st_mtime

    assert content_before == content_after, (
        "state file was modified on the second seed run — idempotency violated"
    )
    assert mtime_before == mtime_after, (
        "state file mtime changed on the second seed run — file was touched"
    )

    # The second run must print a skip notice to stderr
    assert r2.stderr.strip(), (
        "Expected a stderr notice on idempotent skip (FR-3: 'prints a single line to stderr')"
    )


# ---------------------------------------------------------------------------
# test_seed_state_fails_without_project_yaml
# ---------------------------------------------------------------------------


def test_seed_state_fails_without_project_yaml(tmp_path):
    """
    Running seed-state.sh from a repo with no spec/project.yaml exits non-zero
    with a clear stderr message naming the missing file (FR-4 / AC-5).
    """
    assert _SEED_SCRIPT.exists(), (
        f"seed-state.sh not found at {_SEED_SCRIPT}. "
        "T-2 must create the script before this test passes."
    )

    slug = "orc-27-no-project"
    schema = "bugfix"
    fake_repo = tmp_path / "repo-no-project"
    worktree_base = tmp_path / "wt"
    fake_repo.mkdir(parents=True)
    # Deliberately do NOT write spec/project.yaml

    result = _run_seed(
        slug,
        schema,
        repo_root=fake_repo,
        worktree_base=worktree_base,
    )

    assert result.returncode != 0, (
        "seed-state.sh should have exited non-zero when spec/project.yaml is missing, "
        f"but exited {result.returncode}"
    )
    assert "project.yaml" in result.stderr.lower() or "project.yaml" in result.stdout.lower(), (
        f"Expected error message naming 'project.yaml', got:\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
