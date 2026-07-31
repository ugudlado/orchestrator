"""Config-root resolution: env override → vendored repo-local → dev-checkout
config/ → downloaded pack (~/.orchestrator/pack/config)."""
from pathlib import Path

from orchestrator_next.paths import (
    config_root,
    config_root_with_source,
)


def test_explicit_config_wins(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    assert config_root() == Path("/some/config")


def test_unset_falls_back_to_checkout_config(tmp_path, monkeypatch):
    """With no env/vendored config, a dev checkout's config/ wins (engine repo
    itself ships none — the pack tier below covers installs)."""
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    checkout = tmp_path / "checkout" / "config"
    (checkout / "workflows").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: checkout)
    assert paths.config_root() == checkout


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
    """A .orchestrator/config without workflows/ is not a pack — resolution
    falls through to the next tier."""
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "config").mkdir(parents=True)
    checkout = tmp_path / "checkout" / "config"
    (checkout / "workflows").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: checkout)
    assert paths.config_root() == checkout


def test_source_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    root, source = config_root_with_source()
    assert root == Path("/some/config")
    assert source == "env"

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "config" / "workflows").mkdir(parents=True)
    root, source = config_root_with_source()
    assert root == tmp_path / ".orchestrator" / "config"
    assert source == "vendored"

    monkeypatch.delenv("REPO_ROOT", raising=False)
    import orchestrator_next.paths as paths
    checkout = tmp_path / "checkout" / "config"
    (checkout / "workflows").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: checkout)
    root, source = paths.config_root_with_source()
    assert root == checkout
    assert source == "checkout"


def test_pack_tier_and_download_hint(tmp_path, monkeypatch):
    """No env/vendored/checkout config → downloaded pack wins; nothing at all
    → ConfigRootError carrying the download one-liner."""
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: tmp_path / "no-checkout" / "config")
    monkeypatch.setattr(paths, "pack_root", lambda: tmp_path / "pack")

    (tmp_path / "pack" / "config" / "workflows").mkdir(parents=True)
    root, source = paths.config_root_with_source()
    assert root == tmp_path / "pack" / "config"
    assert source == "pack"

    monkeypatch.setattr(paths, "pack_root", lambda: tmp_path / "missing-pack")
    try:
        paths.config_root_with_source()
        raise AssertionError("expected ConfigRootError")
    except paths.ConfigRootError as exc:
        assert "git clone" in str(exc)
