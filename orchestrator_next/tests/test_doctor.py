"""
Tests for orchestrator_next.doctor — all seven check functions, run_all, and _doctor_main.

T-1: RED tests — all must fail with ImportError/AttributeError before doctor.py is implemented.
T-3: CLI wiring tests for _doctor_main.

Test isolation strategy:
  - HOME is monkeypatched to tmp_path so ~/.workflows/ and ~/.claude/agents/ don't
    read real operator state.
  - ORCHESTRATOR_HOME is monkeypatched to a fixture orch_home tree.
  - DuckDB failure path: ensure_schema is monkeypatched to a no-op so the pre-seeded
    DB state is preserved (ensure_schema uses CREATE IF NOT EXISTS, which would otherwise
    create missing tables and make the FAIL path unreachable).
"""
from __future__ import annotations

import os
import sys
import io

import pytest
import yaml
import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def orch_home(tmp_path):
    """Create a minimal ORCHESTRATOR_HOME tree."""
    (tmp_path / "config" / "steps").mkdir(parents=True)
    (tmp_path / "spec" / "changes" / "archive").mkdir(parents=True)
    (tmp_path / "agents").mkdir(parents=True)
    (tmp_path / "scripts" / "inline").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def workflows_home(tmp_path):
    """Patch HOME so ~/.workflows/ is tmp_path/.workflows/."""
    workflows = tmp_path / ".workflows"
    workflows.mkdir()
    return tmp_path, workflows


def _write_state(workflows_dir, change_id, extra=None):
    """Write a minimal valid state.yaml under workflows_dir/<change_id>/."""
    d = workflows_dir / change_id
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "change_id": change_id,
        "phase": "implement",
        "status": "active",
        "workflow_plan": {"implement": {"active": [], "filtered": []}},
        "step_history": [],
    }
    if extra:
        data.update(extra)
    (d / "state.yaml").write_text(yaml.dump(data))
    return d / "state.yaml"


def _write_contract(steps_dir, name, data):
    """Write a flat-form step contract YAML to steps_dir/<name>.yaml."""
    (steps_dir / f"{name}.yaml").write_text(yaml.dump(data))


def _write_dir_contract(steps_dir, name, data):
    """Write a dir-form step contract to steps_dir/<name>/contract.yaml."""
    d = steps_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "contract.yaml").write_text(yaml.dump(data))


# ---------------------------------------------------------------------------
# T-1: check_state_valid
# ---------------------------------------------------------------------------

class TestCheckStateValid:

    def test_check_state_valid_pass(self, tmp_path, monkeypatch):
        """All state.yamls parse cleanly -> PASS."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        _write_state(workflows, "my-feature")
        from orchestrator_next.doctor import check_state_valid
        result = check_state_valid()
        assert result.status == "PASS"

    def test_check_state_valid_no_workflows(self, tmp_path, monkeypatch):
        """No ~/.workflows/ -> PASS (nothing to validate)."""
        home = tmp_path / "empty_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        from orchestrator_next.doctor import check_state_valid
        result = check_state_valid()
        assert result.status == "PASS"

    def test_check_state_valid_malformed_yaml_fails(self, tmp_path, monkeypatch):
        """Malformed YAML -> FAIL with path in detail (UC-E1, AC-5)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        d = workflows / "bad-feature"
        d.mkdir()
        (d / "state.yaml").write_text(": bad: yaml: {unclosed")
        from orchestrator_next.doctor import check_state_valid
        result = check_state_valid()
        assert result.status == "FAIL"
        assert "bad-feature" in result.detail or str(d / "state.yaml") in result.detail


# ---------------------------------------------------------------------------
# T-1: check_active_vs_archive
# ---------------------------------------------------------------------------

class TestCheckActiveVsArchive:

    def test_check_active_vs_archive_pass(self, orch_home, tmp_path, monkeypatch):
        """No overlap between active change IDs and archive basenames -> PASS."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        _write_state(workflows, "new-feature")
        # Archive has a different name
        (orch_home / "spec" / "changes" / "archive" / "old-feature.md").write_text("x")
        from orchestrator_next.doctor import check_active_vs_archive
        result = check_active_vs_archive(orch_home)
        assert result.status == "PASS"

    def test_check_active_vs_archive_substring_match_warns(self, orch_home, tmp_path, monkeypatch):
        """Active change_id is substring of archive basename -> WARN (AC-2, UC-2)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        _write_state(workflows, "my-feature")
        # Archive basename contains "my-feature" as substring
        (orch_home / "spec" / "changes" / "archive" / "my-feature-2025-01-01.md").write_text("x")
        from orchestrator_next.doctor import check_active_vs_archive
        result = check_active_vs_archive(orch_home)
        assert result.status == "WARN"
        assert "my-feature" in result.detail


# ---------------------------------------------------------------------------
# T-1: check_contracts
# ---------------------------------------------------------------------------

class TestCheckContracts:

    def test_check_contracts_pass(self, orch_home):
        """All contracts have id, inputs, outputs -> PASS."""
        _write_contract(orch_home / "config" / "steps", "good", {
            "id": "good", "agent": "inline", "inputs": [], "outputs": [],
            "instruction": "do thing",
        })
        from orchestrator_next.doctor import check_contracts
        result = check_contracts(orch_home)
        assert result.status == "PASS"

    def test_check_contracts_missing_id_fails(self, orch_home):
        """Contract missing `id:` -> FAIL (AC-3, UC-3)."""
        _write_contract(orch_home / "config" / "steps", "no-id", {
            "agent": "inline", "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_contracts
        result = check_contracts(orch_home)
        assert result.status == "FAIL"
        assert "id" in result.detail

    def test_check_contracts_missing_inputs_passes(self, orch_home):
        """ORC-104: missing `inputs:` is now valid (optional, defaults to [])."""
        _write_contract(orch_home / "config" / "steps", "no-inputs", {
            "id": "no-inputs", "outputs": [],
        })
        from orchestrator_next.doctor import check_contracts
        result = check_contracts(orch_home)
        assert result.status == "PASS"

    def test_check_contracts_no_contracts_pass(self, orch_home):
        """No contracts in steps dir -> PASS (nothing to validate)."""
        from orchestrator_next.doctor import check_contracts
        result = check_contracts(orch_home)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# T-1: check_inline_scripts
# ---------------------------------------------------------------------------

class TestCheckInlineScripts:

    def test_check_inline_scripts_pass(self, orch_home):
        """Inline contract with existing script -> PASS."""
        script = orch_home / "scripts" / "inline" / "my-step.sh"
        script.write_text("#!/bin/bash\necho hi")
        _write_contract(orch_home / "config" / "steps", "my-step", {
            "id": "my-step", "inline": True, "run": "scripts/inline/my-step.sh",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_inline_scripts
        result = check_inline_scripts(orch_home)
        assert result.status == "PASS"

    def test_check_inline_scripts_missing_script_fails(self, orch_home):
        """Inline contract with missing script -> FAIL (AC-4, UC-4)."""
        _write_contract(orch_home / "config" / "steps", "missing-step", {
            "id": "missing-step", "inline": True, "run": "scripts/inline/missing.sh",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_inline_scripts
        result = check_inline_scripts(orch_home)
        assert result.status == "FAIL"
        assert "missing.sh" in result.detail or "missing-step" in result.detail

    def test_check_inline_scripts_non_inline_skipped(self, orch_home):
        """Non-inline contract with no script -> PASS (not checked)."""
        _write_contract(orch_home / "config" / "steps", "agent-step", {
            "id": "agent-step", "agent": "some-agent", "inline": False,
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_inline_scripts
        result = check_inline_scripts(orch_home)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# T-1: check_agent_files
# ---------------------------------------------------------------------------

class TestCheckAgentFiles:

    def test_check_agent_files_pass(self, orch_home, tmp_path, monkeypatch):
        """Agent file exists in orch_home/agents/ -> PASS."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo_root = tmp_path / "repo"
        (orch_home / "agents" / "my-agent.md").write_text("# my-agent")
        _write_contract(orch_home / "config" / "steps", "uses-agent", {
            "id": "uses-agent", "agent": "my-agent",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "PASS"

    def test_check_agent_files_inline_sentinel_skipped(self, orch_home, tmp_path, monkeypatch):
        """Contract with agent: inline is not flagged (AC-7, UC-E3)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo_root = tmp_path / "repo"
        _write_contract(orch_home / "config" / "steps", "inline-step", {
            "id": "inline-step", "agent": "inline",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "PASS"

    def test_check_agent_files_missing_agent_warns(self, orch_home, tmp_path, monkeypatch):
        """Agent file missing in all locations -> WARN."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo_root = tmp_path / "repo"
        (home / ".claude" / "agents").mkdir(parents=True)
        _write_contract(orch_home / "config" / "steps", "ghost-step", {
            "id": "ghost-step", "agent": "ghost-agent",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "WARN"
        assert "ghost-agent" in result.detail

    def test_check_agent_files_no_agent_field_skipped(self, orch_home, tmp_path, monkeypatch):
        """Contract with no agent: field -> PASS (skipped)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo_root = tmp_path / "repo"
        _write_contract(orch_home / "config" / "steps", "no-agent", {
            "id": "no-agent", "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "PASS"

    def test_check_agent_files_dir_form_missing_agent_warns(self, orch_home, tmp_path, monkeypatch):
        """UC-4: dir-form (steps/<id>/contract.yaml) agent refs are validated too."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        (home / ".claude" / "agents").mkdir(parents=True)
        repo_root = tmp_path / "repo"
        _write_dir_contract(orch_home / "config" / "steps", "dir-step", {
            "id": "dir-step", "agent": "phantom-agent",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "WARN"
        assert "phantom-agent" in result.detail
        assert "dir-step" in result.detail

    def test_check_agent_files_dir_form_present_passes(self, orch_home, tmp_path, monkeypatch):
        """Dir-form contract whose agent .md exists -> PASS."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo_root = tmp_path / "repo"
        (orch_home / "agents" / "real-agent.md").write_text("# real-agent")
        _write_dir_contract(orch_home / "config" / "steps", "dir-step", {
            "id": "dir-step", "agent": "real-agent",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "PASS"

    def test_check_agent_files_repo_override_resolves(self, orch_home, tmp_path, monkeypatch):
        """Agent provided only via repo .orchestrator/agents override -> PASS."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo_root = tmp_path / "repo"
        (repo_root / ".orchestrator" / "agents").mkdir(parents=True)
        (repo_root / ".orchestrator" / "agents" / "override-agent.md").write_text("# override")
        _write_dir_contract(orch_home / "config" / "steps", "ov-step", {
            "id": "ov-step", "agent": "override-agent",
            "inputs": [], "outputs": [],
        })
        from orchestrator_next.doctor import check_agent_files
        result = check_agent_files(repo_root, orch_home)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# T-1: check_duckdb_schema
# ---------------------------------------------------------------------------

class TestCheckDuckdbSchema:

    def test_check_duckdb_schema_pass(self, tmp_path):
        """Fresh DB with ensure_schema applied -> PASS."""
        db_path = tmp_path / "metrics.duckdb"
        from orchestrator_next.doctor import check_duckdb_schema
        result = check_duckdb_schema(db_path)
        assert result.status == "PASS"

    def test_check_duckdb_schema_missing_tool_calls_fails(self, tmp_path, monkeypatch):
        """DB with step_events but missing tool_calls -> FAIL (AC-6, UC-E2).

        ensure_schema is monkeypatched to a no-op so the pre-seeded DB state
        is preserved (ensure_schema uses CREATE IF NOT EXISTS, which would
        otherwise create the missing table and make this FAIL path unreachable).
        """
        db_path = tmp_path / "partial.duckdb"
        # Seed: only create step_events, not tool_calls
        conn = duckdb.connect(str(db_path))
        conn.execute("""
            CREATE TABLE step_events (
                repo_root VARCHAR, change_id VARCHAR, phase VARCHAR,
                step_id VARCHAR, attempt INTEGER, agent_name VARCHAR,
                status VARCHAR,
                PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
            )
        """)
        conn.close()

        # Patch ensure_schema to no-op so the pre-existing partial state is preserved
        import orchestrator_next.doctor as doctor_mod
        monkeypatch.setattr(doctor_mod, "_ensure_schema_noop", lambda conn: None, raising=False)

        # Patch the upsert module's ensure_schema to no-op within doctor
        import orchestrator_next.upsert as upsert_mod
        monkeypatch.setattr(upsert_mod, "ensure_schema", lambda conn: None)

        from orchestrator_next.doctor import check_duckdb_schema
        result = check_duckdb_schema(db_path)
        assert result.status == "FAIL"
        assert "tool_calls" in result.detail


# ---------------------------------------------------------------------------
# T-1: check_workflow_plans
# ---------------------------------------------------------------------------

class TestCheckWorkflowPlans:

    def test_check_workflow_plans_pass(self, orch_home, tmp_path, monkeypatch):
        """Active steps all have contracts -> PASS."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        _write_contract(orch_home / "config" / "steps", "my-step", {
            "id": "my-step", "inputs": [], "outputs": [],
        })
        _write_state(workflows, "my-feature", extra={
            "workflow_plan": {"implement": {"active": ["my-step"], "filtered": []}},
        })
        from orchestrator_next.doctor import check_workflow_plans
        result = check_workflow_plans(orch_home)
        assert result.status == "PASS"

    def test_check_workflow_plans_missing_contract_warns(self, orch_home, tmp_path, monkeypatch):
        """Active step without a contract -> WARN."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        _write_state(workflows, "my-feature", extra={
            "workflow_plan": {"implement": {"active": ["ghost-step"], "filtered": []}},
        })
        from orchestrator_next.doctor import check_workflow_plans
        result = check_workflow_plans(orch_home)
        assert result.status == "WARN"
        assert "ghost-step" in result.detail

    def test_check_workflow_plans_normalizes_dict_and_if_flag(self, orch_home, tmp_path, monkeypatch):
        """Dict step (with id key) and 'step if flag' format are both normalized correctly."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        workflows = home / ".workflows"
        workflows.mkdir()
        _write_contract(orch_home / "config" / "steps", "dict-step", {
            "id": "dict-step", "inputs": [], "outputs": [],
        })
        _write_contract(orch_home / "config" / "steps", "flag-step", {
            "id": "flag-step", "inputs": [], "outputs": [],
        })
        _write_state(workflows, "my-feature", extra={
            "workflow_plan": {"implement": {
                "active": [
                    {"id": "dict-step", "some": "data"},
                    "flag-step if some_condition",
                ],
                "filtered": [],
            }},
        })
        from orchestrator_next.doctor import check_workflow_plans
        result = check_workflow_plans(orch_home)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# T-1: run_all exit code logic
# ---------------------------------------------------------------------------

class TestRunAllExitCodes:

    def test_run_all_exit_code_all_pass(self, orch_home, tmp_path, monkeypatch):
        """All 7 checks PASS -> exit 0 (AC-1, UC-1)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        (home / ".workflows").mkdir()
        # Point metrics DB to a fresh temp DB
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))
        from orchestrator_next.doctor import run_all
        code = run_all(None)
        assert code == 0

    def test_run_all_exit_code_warn_only_is_0(self, orch_home, tmp_path, monkeypatch):
        """At least one WARN and no FAIL -> exit 0 (AC-10, ORC-18)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        workflows = home / ".workflows"
        workflows.mkdir()
        # Create stale state: active change_id matches archive basename
        _write_state(workflows, "stale-feat")
        (orch_home / "spec" / "changes" / "archive" / "stale-feat-2025.md").write_text("x")
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))
        from orchestrator_next.doctor import run_all
        code = run_all(None)
        assert code == 0

    def test_run_all_exit_code_any_fail_is_2(self, orch_home, tmp_path, monkeypatch):
        """At least one FAIL -> exit 2 (AC-10, UC-3)."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        (home / ".workflows").mkdir()
        # Bad contract: missing id
        _write_contract(orch_home / "config" / "steps", "bad", {
            "agent": "inline", "inputs": [], "outputs": [],
        })
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))
        from orchestrator_next.doctor import run_all
        code = run_all(None)
        assert code == 2


# ---------------------------------------------------------------------------
# T-3: CLI wiring tests for _doctor_main
# ---------------------------------------------------------------------------

class TestDoctorMain:

    def test_doctor_main_without_orchestrator_home_errors(self, monkeypatch, capsys):
        """Unset ORCHESTRATOR_HOME -> non-zero return, stderr has ORCHESTRATOR_HOME (AC-8, UC-E4)."""
        monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
        from orchestrator_next.doctor import _doctor_main
        code = _doctor_main([])
        assert code != 0
        captured = capsys.readouterr()
        assert "ORCHESTRATOR_HOME" in captured.err

    def test_doctor_main_with_valid_env_returns_int(self, orch_home, tmp_path, monkeypatch):
        """Valid env -> returns integer 0, 1, or 2."""
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        (home / ".workflows").mkdir()
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))
        from orchestrator_next.doctor import _doctor_main
        code = _doctor_main([])
        assert isinstance(code, int)
        assert code in (0, 1, 2)

    def test_doctor_main_help_flag(self):
        """--help -> SystemExit with code 0 (argparse default)."""
        from orchestrator_next.doctor import _doctor_main
        with pytest.raises(SystemExit) as exc_info:
            _doctor_main(["--help"])
        assert exc_info.value.code == 0

    def test_doctor_main_returns_int_on_real_repo(self, tmp_path, monkeypatch):
        """Smoke: _doctor_main([]) on real repo returns int exit code (CLI smoke test)."""
        real_orch_home = "/Users/spidey/code/orchestrator"
        if not os.path.isdir(real_orch_home):
            pytest.skip("real orchestrator repo not available")
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        (home / ".workflows").mkdir()
        monkeypatch.setenv("ORCHESTRATOR_HOME", real_orch_home)
        db_path = tmp_path / "metrics.duckdb"
        monkeypatch.setenv("METRICS_DB", str(db_path))
        from orchestrator_next.doctor import _doctor_main
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = _doctor_main([])
        assert isinstance(code, int)
        assert code in (0, 1, 2)
        assert len(buf.getvalue()) > 0
