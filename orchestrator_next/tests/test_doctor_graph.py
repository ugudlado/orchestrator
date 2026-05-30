"""
T-2 (ORC-18): RED tests for new doctor graph checks and run_all exit semantics.

These tests import check_* functions that do not exist until T-3 implements them.
run_all WARN-only exit-code tests fail until T-3 changes WARN exit from 1 → 0.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo_root(tmp_path):
    """Minimal repo root (may hold .orchestrator overrides)."""
    (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture()
def orch_home(tmp_path):
    """Minimal ORCHESTRATOR_HOME tree for graph checks."""
    root = tmp_path / "orch_home"
    (root / "config" / "workflows").mkdir(parents=True)
    (root / "config" / "steps").mkdir(parents=True)
    (root / "config" / "templates" / "feature").mkdir(parents=True)
    (root / "agents").mkdir(parents=True)
    (root / "spec" / "changes" / "archive").mkdir(parents=True)
    return root


def _write_workflow(workflows_dir, name: str, steps: list) -> None:
    (workflows_dir / f"{name}.yaml").write_text(yaml.dump({"steps": steps}))


def _write_dir_contract(steps_dir, step_id: str, data: dict, *, prompt: str = "# prompt\n") -> None:
    d = steps_dir / step_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "contract.yaml").write_text(yaml.dump(data))
    (d / "prompt.md").write_text(prompt)


def _write_agent(agents_dir, name: str) -> None:
    (agents_dir / f"{name}.md").write_text(f"---\ntools: []\n---\n# {name}\n")


# ---------------------------------------------------------------------------
# Symlinks (AC-2)
# ---------------------------------------------------------------------------

class TestCheckSymlinks:

    def test_stale_symlink_reports_bad_target(self, repo_root, orch_home):
        """Stale symlink → WARN/FAIL naming the broken target path."""
        stale = orch_home / "config" / "stale-link"
        missing_target = orch_home / "does-not-exist-target"
        stale.symlink_to(missing_target)
        from orchestrator_next.doctor import check_symlinks

        result = check_symlinks(repo_root, orch_home)
        assert result.status in ("WARN", "FAIL")
        assert str(missing_target) in result.detail or "does-not-exist-target" in result.detail


# ---------------------------------------------------------------------------
# ORCHESTRATOR_HOME (AC-3)
# ---------------------------------------------------------------------------

class TestCheckOrchestratorHome:

    def test_worktree_env_mismatch_fails(self, repo_root, orch_home, tmp_path, monkeypatch):
        """ORCHESTRATOR_HOME set to worktree while install symlink points at main → FAIL."""
        main_install = tmp_path / "main_orchestrator"
        main_install.mkdir()
        worktree = tmp_path / "feature_worktree"
        worktree.mkdir()

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        config_dir = fake_home / ".config" / "orchestrator"
        config_dir.parent.mkdir(parents=True)
        config_dir.symlink_to(main_install)

        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(worktree))

        from orchestrator_next.doctor import check_orchestrator_home

        result = check_orchestrator_home(repo_root, orch_home)
        assert result.status == "FAIL"
        assert str(worktree) in result.detail
        assert str(main_install) in result.detail or "expected" in result.detail.lower()


# ---------------------------------------------------------------------------
# Schema → step graph (AC-5)
# ---------------------------------------------------------------------------

class TestCheckSchemaStepGraph:

    def test_missing_step_contract_fails(self, repo_root, orch_home):
        """Workflow references step with no contract → FAIL names schema and step."""
        _write_workflow(
            orch_home / "config" / "workflows",
            "feature",
            ["present-step", "ghost-step"],
        )
        _write_dir_contract(
            orch_home / "config" / "steps",
            "present-step",
            {
                "id": "present-step",
                "version": 1,
                "kind": "agent",
                "agent": "developer",
                "inputs": [],
                "outputs": [],
            },
        )
        _write_agent(orch_home / "agents", "developer")

        from orchestrator_next.doctor import check_schema_step_graph

        result = check_schema_step_graph(repo_root, orch_home)
        assert result.status == "FAIL"
        assert "feature" in result.detail
        assert "ghost-step" in result.detail
        assert "contract" in result.detail.lower()


# ---------------------------------------------------------------------------
# Template graph (AC-8)
# ---------------------------------------------------------------------------

class TestCheckContractTemplateGraph:

    def test_missing_template_path_fails(self, repo_root, orch_home):
        """Contract references missing template → FAIL names contract and path."""
        missing = "config/templates/feature/missing-template.md"
        _write_workflow(orch_home / "config" / "workflows", "feature", ["tmpl-step"])
        _write_dir_contract(
            orch_home / "config" / "steps",
            "tmpl-step",
            {
                "id": "tmpl-step",
                "version": 1,
                "kind": "agent",
                "agent": "developer",
                "inputs": [],
                "outputs": [],
                "template_paths": [missing],
            },
        )
        _write_agent(orch_home / "agents", "developer")

        from orchestrator_next.doctor import check_contract_template_graph

        result = check_contract_template_graph(repo_root, orch_home)
        assert result.status == "FAIL"
        assert "tmpl-step" in result.detail
        assert "missing-template.md" in result.detail


# ---------------------------------------------------------------------------
# Override resolution (AC-12)
# ---------------------------------------------------------------------------

class TestOverrideResolution:

    def test_repo_override_resolves_before_global(self, repo_root, orch_home):
        """Step contract only under .orchestrator/ → schema graph PASS (override wins)."""
        (repo_root / ".orchestrator" / "workflows").mkdir(parents=True, exist_ok=True)
        _write_workflow(repo_root / ".orchestrator" / "workflows", "feature", ["override-only"])

        _write_workflow(orch_home / "config" / "workflows", "feature", ["override-only"])
        # Global orch_home has workflow ref but NO global contract — override supplies it.
        _write_dir_contract(
            repo_root / ".orchestrator" / "steps",
            "override-only",
            {
                "id": "override-only",
                "version": 1,
                "kind": "agent",
                "agent": "developer",
                "inputs": [],
                "outputs": [],
            },
        )
        _write_agent(orch_home / "agents", "developer")

        from orchestrator_next.doctor import check_schema_step_graph

        result = check_schema_step_graph(repo_root, orch_home)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# run_all exit codes (AC-10 / AC-11)
# ---------------------------------------------------------------------------

class TestRunAllExitCodes:

    def test_warn_only_exit_zero(self, orch_home, tmp_path, monkeypatch):
        """WARN-only run → exit 0 (was 1 before ORC-18)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        workflows = home / ".workflows"
        workflows.mkdir()
        # Trigger WARN from existing check_active_vs_archive
        state_dir = workflows / "stale-feat"
        state_dir.mkdir()
        (state_dir / "state.yaml").write_text(yaml.dump({
            "change_id": "stale-feat",
            "phase": "implement",
            "workflow_plan": {"implement": {"active": [], "filtered": []}},
            "step_history": [],
        }))
        (orch_home / "spec" / "changes" / "archive" / "stale-feat-2025.md").write_text("x")
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))

        from orchestrator_next.doctor import run_all

        assert run_all(None) == 0

    def test_fail_exit_nonzero(self, orch_home, tmp_path, monkeypatch):
        """Any FAIL → exit 2."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        (home / ".workflows").mkdir()
        # Flat legacy contract missing id → FAIL from check_contracts
        (orch_home / "config" / "steps" / "bad.yaml").write_text(yaml.dump({
            "agent": None,
            "inputs": [],
            "outputs": [],
        }))
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))

        from orchestrator_next.doctor import run_all

        assert run_all(None) == 2
