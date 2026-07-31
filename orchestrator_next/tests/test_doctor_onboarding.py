"""Tests for the distribution-improvements onboarding checks in doctor.py:
config source reporting, spec/project.yaml presence/parse, git repo present,
and ticketing backend reachability."""
from __future__ import annotations


import yaml

from orchestrator_next.doctor import (
    check_config_source,
    check_git_repo,
    check_project_yaml,
    check_ticketing_backend,
)


def test_check_config_source_reports_label(tmp_path):
    result = check_config_source("bundled", tmp_path)
    assert result.status == "PASS"
    assert "bundled" in result.detail
    assert str(tmp_path) in result.detail


def test_check_project_yaml_missing_warns_with_init_hint(tmp_path):
    result = check_project_yaml(tmp_path)
    assert result.status == "WARN"
    assert "orchestrator init" in result.detail


def test_check_project_yaml_valid_passes(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "project.yaml").write_text(yaml.safe_dump({"version": 1, "ticketing": "backlog"}))
    result = check_project_yaml(tmp_path)
    assert result.status == "PASS"


def test_check_project_yaml_unparsable_warns(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "project.yaml").write_text(":::not yaml:::\n\t- broken")
    result = check_project_yaml(tmp_path)
    assert result.status == "WARN"


def test_check_git_repo_passes_inside_repo(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = check_git_repo(tmp_path)
    assert result.status == "PASS"


def test_check_git_repo_warns_outside_repo(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = check_git_repo(outside)
    assert result.status == "WARN"


def test_check_ticketing_backend_skips_when_no_project_yaml(tmp_path):
    result = check_ticketing_backend(tmp_path)
    assert result.status == "PASS"
    assert "skipped" in result.detail


def test_check_ticketing_backend_skips_when_unset(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "project.yaml").write_text(yaml.safe_dump({"version": 1}))
    result = check_ticketing_backend(tmp_path)
    assert result.status == "PASS"
    assert "skipped" in result.detail


def test_check_ticketing_backend_backlog_warns_missing_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.delenv("BACKLOG_TOKEN", raising=False)
    monkeypatch.delenv("BACKLOG_PROJECT", raising=False)
    monkeypatch.delenv("BACKLOG_PROJECT_ID", raising=False)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "project.yaml").write_text(yaml.safe_dump({"version": 1, "ticketing": "backlog"}))
    result = check_ticketing_backend(tmp_path)
    assert result.status == "WARN"
    assert "BACKLOG_URL" in result.detail


def test_check_ticketing_backend_backlog_passes_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_URL", "https://example.test")
    monkeypatch.setenv("BACKLOG_TOKEN", "tok")
    monkeypatch.setenv("BACKLOG_PROJECT_ID", "proj-1")
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "project.yaml").write_text(yaml.safe_dump({"version": 1, "ticketing": "backlog"}))
    result = check_ticketing_backend(tmp_path)
    assert result.status == "PASS"


def test_repo_root_from_env_prefers_git_toplevel_over_orch_home(tmp_path, monkeypatch):
    """When REPO_ROOT/ORCHESTRATOR_REPO_ROOT are unset (bundled-config fallback
    case, T1), orch_home (config_root.parent) is not the consumer repo in a
    wheel install — _repo_root_from_env must not silently return it while a
    real git toplevel is discoverable from cwd."""
    import subprocess

    from orchestrator_next.doctor import _repo_root_from_env

    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)

    fake_orch_home = tmp_path / "site-packages" / "orchestrator_next"
    fake_orch_home.mkdir(parents=True)

    result = _repo_root_from_env(fake_orch_home)
    assert result == repo.resolve()
    assert result != fake_orch_home.resolve()


def test_check_ticketing_backend_linear_passes(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "project.yaml").write_text(yaml.safe_dump({"version": 1, "ticketing": "linear"}))
    result = check_ticketing_backend(tmp_path)
    assert result.status == "PASS"


def test_check_commit_verification_warns_without_hooks(tmp_path):
    from orchestrator_next.doctor import check_commit_verification

    result = check_commit_verification(tmp_path)
    assert result.status == "WARN"
    assert "pre-commit" in result.detail


def test_check_commit_verification_passes_with_pre_commit_config(tmp_path):
    from orchestrator_next.doctor import check_commit_verification

    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    result = check_commit_verification(tmp_path)
    assert result.status == "PASS"
    assert ".pre-commit-config.yaml" in result.detail
