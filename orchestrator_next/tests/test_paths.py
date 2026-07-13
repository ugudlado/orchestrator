"""Config-root resolution is explicit — no cwd fallback (ORC packaging split)."""
from pathlib import Path

import pytest

from orchestrator_next.paths import ConfigRootError, bundled_config_root, config_root


def test_explicit_config_wins(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.setenv("ORCHESTRATOR_HOME", "/other/home")
    assert config_root() == Path("/some/config")


def test_legacy_home_appends_config(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_HOME", "/other/home")
    assert config_root() == Path("/other/home/config")


def test_unset_raises_no_cwd_fallback(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    with pytest.raises(ConfigRootError):
        config_root()


def test_bundled_config_root_exists():
    # Dev checkout: repo config/. Wheel install: orchestrator_next/config.
    assert bundled_config_root().is_dir()
