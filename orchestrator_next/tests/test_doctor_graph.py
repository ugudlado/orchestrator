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
    (root / "config" / "agents.yaml").write_text("agents: {}\n")
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
# Config root validation (replaces the old ORCHESTRATOR_HOME == ~/.config check)
# ---------------------------------------------------------------------------

class TestCheckConfigRoot:

    def test_valid_config_root_passes(self, tmp_path):
        """A config dir with workflows/, steps/, agents.yaml → PASS."""
        cfg = tmp_path / "config"
        (cfg / "workflows").mkdir(parents=True)
        (cfg / "steps").mkdir()
        (cfg / "agents.yaml").write_text("agents: {}\n")

        from orchestrator_next.doctor import check_config_root

        result = check_config_root(cfg)
        assert result.status == "PASS"

    def test_missing_entry_fails(self, tmp_path):
        """A config dir missing agents.yaml → FAIL naming the missing entry."""
        cfg = tmp_path / "config"
        (cfg / "workflows").mkdir(parents=True)
        (cfg / "steps").mkdir()
        # no agents.yaml

        from orchestrator_next.doctor import check_config_root

        result = check_config_root(cfg)
        assert result.status == "FAIL"
        assert "agents.yaml" in result.detail

    def test_nonexistent_root_fails(self, tmp_path):
        from orchestrator_next.doctor import check_config_root

        result = check_config_root(tmp_path / "nope")
        assert result.status == "FAIL"


# ---------------------------------------------------------------------------
# Rule 1: workflow steps resolve to contracts (config_root-anchored)
# ---------------------------------------------------------------------------

class TestCheckWorkflowStepsResolve:

    def test_missing_step_contract_fails(self, orch_home):
        """Workflow references a step with no contract under steps/ → FAIL."""
        config_root = orch_home / "config"
        _write_workflow(config_root / "workflows", "feature", ["present-step", "ghost-step"])
        _write_dir_contract(
            config_root / "steps",
            "present-step",
            {"id": "present-step", "version": 1, "kind": "agent", "agent": "developer"},
        )

        from orchestrator_next.doctor import check_workflow_steps_resolve

        result = check_workflow_steps_resolve(config_root)
        assert result.status == "FAIL"
        assert "feature" in result.detail
        assert "ghost-step" in result.detail


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
        # Contract with no agent/run/main → FAIL from check_step_dispatch_kind (rule 2)
        (orch_home / "config" / "steps" / "bad.yaml").write_text(yaml.dump({
            "id": "bad",
            "agent": None,
        }))
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))

        from orchestrator_next.doctor import run_all

        assert run_all(None) == 2
