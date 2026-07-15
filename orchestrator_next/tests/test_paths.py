"""Config-root resolution is explicit — no cwd fallback (ORC packaging split)."""
from pathlib import Path

import pytest

from orchestrator_next.paths import ConfigRootError, bundled_config_root, config_root


def test_explicit_config_wins(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    assert config_root() == Path("/some/config")


def test_unset_raises_no_cwd_fallback(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    with pytest.raises(ConfigRootError):
        config_root()


def test_bundled_config_root_exists():
    # Dev checkout: repo config/. Wheel install: orchestrator_next/config.
    assert bundled_config_root().is_dir()


def test_repo_local_config_wins_over_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "config" / "workflows").mkdir(parents=True)
    assert config_root() == tmp_path / ".orchestrator" / "config"


def test_explicit_env_wins_over_repo_local(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "config" / "workflows").mkdir(parents=True)
    assert config_root() == Path("/some/config")


def test_repo_local_skipped_without_workflows_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "config").mkdir(parents=True)
    with pytest.raises(ConfigRootError):
        config_root()
