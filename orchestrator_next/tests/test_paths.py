"""Config-root and workflow-ref resolution for named packs under .orchestrator/."""
from pathlib import Path

import pytest

from orchestrator_next.paths import (
    WorkflowRefError,
    config_root,
    config_root_with_source,
    list_config_packs,
    resolve_workflow_ref,
)


def test_explicit_config_wins(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    assert config_root() == Path("/some/config")


def test_single_named_pack_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    pack = tmp_path / ".orchestrator" / "mypack"
    (pack / "workflows").mkdir(parents=True)
    assert config_root() == pack
    assert list_config_packs(tmp_path) == [("mypack", pack)]


def test_multiple_packs_without_env_errors(tmp_path, monkeypatch):
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    for name in ("mypack", "mypack1"):
        (tmp_path / ".orchestrator" / name / "workflows").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: tmp_path / "nope" / "config")
    monkeypatch.setattr(paths, "pack_root", lambda: tmp_path / "missing-pack")
    with pytest.raises(paths.ConfigRootError, match="multiple config packs"):
        config_root()


def test_legacy_flat_as_default_pack(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "workflows").mkdir(parents=True)
    root, source = config_root_with_source()
    assert root == tmp_path / ".orchestrator"
    assert source == "vendored"
    assert list_config_packs(tmp_path)[0][0] == "default"


def test_unset_falls_back_to_checkout_config(tmp_path, monkeypatch):
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    checkout = tmp_path / "checkout" / "config"
    (checkout / "workflows").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: checkout)
    assert paths.config_root() == checkout


def test_unique_workflow_bare_name(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    pack = tmp_path / ".orchestrator" / "mypack"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "feature.yaml").write_text("steps: []\n")
    p, wf, root = resolve_workflow_ref("feature", tmp_path)
    assert (p, wf, root) == ("mypack", "feature", pack)


def test_ambiguous_workflow_requires_pack_prefix(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    for name in ("mypack", "mypack1"):
        wf = tmp_path / ".orchestrator" / name / "workflows"
        wf.mkdir(parents=True)
        (wf / "feature.yaml").write_text("steps: []\n")
    with pytest.raises(WorkflowRefError, match="not unique"):
        resolve_workflow_ref("feature", tmp_path)
    p, wf, root = resolve_workflow_ref("mypack1/feature", tmp_path)
    assert p == "mypack1"
    assert wf == "feature"
    assert root == tmp_path / ".orchestrator" / "mypack1"


def test_pack_tier_and_download_hint(tmp_path, monkeypatch):
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
    with pytest.raises(paths.ConfigRootError) as exc:
        paths.config_root_with_source()
    assert "config pull" in str(exc.value) or "git clone" in str(exc.value)
