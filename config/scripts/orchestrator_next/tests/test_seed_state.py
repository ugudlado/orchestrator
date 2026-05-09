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
_REPO_ROOT = _HERE.parents[3]
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


def _run_seed(
    slug: str,
    schema: str,
    *,
    repo_root: Path,
    state_dir: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run seed-state.sh with the given slug/schema and isolated env vars.

    PYTHONPATH is set to the real config/scripts directory so the subprocess
    can import orchestrator_next regardless of where repo_root (the fake test
    repo) points.  The script respects an existing PYTHONPATH by prepending.
    """
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["WORKFLOW_STATE_DIR"] = str(state_dir)
    env["ORCHESTRATOR_HOME"] = _ORCHESTRATOR_HOME
    # Ensure orchestrator_next is importable inside the subprocess:
    # _HERE.parents[1] == config/scripts/ (parent of orchestrator_next package)
    real_scripts_dir = str(_HERE.parents[1])
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{real_scripts_dir}:{existing_pypath}" if existing_pypath else real_scripts_dir
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_SEED_SCRIPT), slug, schema],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# test_seed_state_produces_dispatch_ready_pair
# ---------------------------------------------------------------------------


def test_seed_state_produces_dispatch_ready_pair(tmp_path):
    """
    RED before T-2: asserts seed-state.sh exists at the expected path.

    After T-2 the script is present, the seeder exits 0, state.yaml and
    plan.yaml are written, and `orchestrator next <state.yaml>` returns a
    run_step or run_inline action JSON (not exit code 3).

    Failure before T-2: AssertionError naming the missing script path.
    """
    # ---- This assertion is the RED gate: it fails before T-2 ----
    assert _SEED_SCRIPT.exists(), (
        f"seed-state.sh not found at {_SEED_SCRIPT}. "
        "T-2 must create skills/orchestrate/scripts/seed-state.sh before this test passes."
    )

    # ---- Everything below runs only after T-2 ships ----
    slug = "orc-27-test"
    schema = "bugfix"
    fake_repo = tmp_path / "repo"
    state_dir = tmp_path / "state"
    _write_project_yaml(fake_repo)

    result = _run_seed(slug, schema, repo_root=fake_repo, state_dir=state_dir)
    assert result.returncode == 0, (
        f"seed-state.sh exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    state_yaml_path = state_dir / slug / "state.yaml"
    plan_yaml_path = state_dir / slug / "plan.yaml"

    assert state_yaml_path.exists(), "seed-state.sh did not write state.yaml"
    assert plan_yaml_path.exists(), "seed-state.sh did not write plan.yaml (generate_plan not called)"

    # Verify state.yaml round-trips through parser.load_state
    scripts_parent = str(_HERE.parents[2])  # config/scripts/
    if scripts_parent not in sys.path:
        sys.path.insert(0, scripts_parent)
    from orchestrator_next.parser import load_state  # noqa: E402 (import inside test)

    state = load_state(str(state_yaml_path))
    assert state.change_id, "state.yaml missing change_id"
    assert state.phase, "state.yaml missing phase"
    assert state.workflow_plan, "state.yaml missing workflow_plan"

    # ORC-34 regression: both created_at and started_at must be present and equal
    state_raw = yaml.safe_load(state_yaml_path.read_text())
    assert "created_at" in state_raw, "state.yaml missing created_at"
    assert "started_at" in state_raw, "state.yaml missing started_at (ORC-34: seed-state.sh must write started_at)"
    assert state_raw["started_at"] == state_raw["created_at"], (
        f"started_at ({state_raw.get('started_at')!r}) != created_at ({state_raw.get('created_at')!r})"
    )

    # Verify orchestrator next accepts the seeded pair (AC-6: not exit 3)
    orchestrator_bin = _REPO_ROOT / "bin" / "orchestrator"
    next_env = os.environ.copy()
    next_env["ORCHESTRATOR_HOME"] = _ORCHESTRATOR_HOME
    next_result = subprocess.run(
        [str(orchestrator_bin), "next", str(state_yaml_path)],
        capture_output=True,
        text=True,
        env=next_env,
    )
    assert next_result.returncode != 3, (
        f"`orchestrator next` returned exit 3 (state.yaml not found) — "
        f"the seeder did not produce a file the CLI accepts.\n"
        f"stderr: {next_result.stderr}"
    )
    assert next_result.returncode == 0, (
        f"`orchestrator next` exited {next_result.returncode}\n"
        f"stdout: {next_result.stdout}\n"
        f"stderr: {next_result.stderr}"
    )

    try:
        action = json.loads(next_result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"`orchestrator next` stdout is not valid JSON: {exc}\n"
            f"stdout: {next_result.stdout!r}"
        )

    assert "agent" in action or "run" in action, (
        f"Expected agent or run key in response (ORC-45 two-path), got: {list(action.keys())!r}\n"
        f"Full action: {action}"
    )
    assert action.get("step_id") == "workflow-init", (
        f"Expected first step to be workflow-init, got: {action.get('step_id')!r}"
    )


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
    state_dir = tmp_path / "state"
    _write_project_yaml(fake_repo)

    # First run — creates state.yaml
    r1 = _run_seed(slug, schema, repo_root=fake_repo, state_dir=state_dir)
    assert r1.returncode == 0, f"First seed failed: {r1.stderr}"

    state_yaml_path = state_dir / slug / "state.yaml"
    assert state_yaml_path.exists()
    content_before = state_yaml_path.read_text()
    mtime_before = state_yaml_path.stat().st_mtime

    # Second run — must not overwrite
    r2 = _run_seed(slug, schema, repo_root=fake_repo, state_dir=state_dir)
    assert r2.returncode == 0, f"Second (idempotent) seed failed: {r2.stderr}"

    content_after = state_yaml_path.read_text()
    mtime_after = state_yaml_path.stat().st_mtime

    assert content_before == content_after, (
        "state.yaml was modified on the second seed run — idempotency violated"
    )
    assert mtime_before == mtime_after, (
        "state.yaml mtime changed on the second seed run — file was touched"
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
    state_dir = tmp_path / "state"
    fake_repo.mkdir(parents=True)
    # Deliberately do NOT write spec/project.yaml

    result = _run_seed(slug, schema, repo_root=fake_repo, state_dir=state_dir)

    assert result.returncode != 0, (
        "seed-state.sh should have exited non-zero when spec/project.yaml is missing, "
        f"but exited {result.returncode}"
    )
    assert "project.yaml" in result.stderr.lower() or "project.yaml" in result.stdout.lower(), (
        f"Expected error message naming 'project.yaml', got:\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
