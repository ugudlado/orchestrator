"""Config-root and workflow-ref resolution for named packs under .orchestrator/."""
from pathlib import Path

import pytest

from orchestrator_next.paths import (
    WorkflowRefError,
    config_root,
    config_root_with_source,
    list_config_packs,
    resolve_workflow_ref,
    workflow_mode,
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


def test_workflow_mode_reads_session_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    wf_dir = tmp_path / ".orchestrator" / "mypack" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "research.yaml").write_text("mode: session\nsteps: []\n")
    (wf_dir / "feature.yaml").write_text("steps: []\n")
    assert workflow_mode("research", tmp_path) == "session"
    assert workflow_mode("feature", tmp_path) == "ticket"


def test_checkout_and_global_pack_fallbacks_are_gone(tmp_path, monkeypatch):
    """Plan 3.2: the ladder is env -> single vendored pack, and nothing else.

    Both deleted levels are staged here (an engine checkout `config/` and a
    `~/.orchestrator/pack/config`) — resolution must still fail, or a run could
    silently execute a pack no lock ever vouched for.
    """
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    checkout = tmp_path / "checkout" / "config"
    (checkout / "workflows").mkdir(parents=True)
    (tmp_path / "pack" / "config" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_config_root", lambda: checkout)
    monkeypatch.setattr(paths, "pack_root", lambda: tmp_path / "pack")
    with pytest.raises(paths.ConfigRootError):
        paths.config_root()


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


def test_resolution_ladder_has_exactly_two_levels(tmp_path, monkeypatch):
    import orchestrator_next.paths as paths

    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)

    # Level 1: explicit env.
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(tmp_path / "explicit"))
    assert paths.config_root_with_source() == (tmp_path / "explicit", "env")

    # Level 2: exactly one vendored pack.
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    pack = tmp_path / ".orchestrator" / "only"
    (pack / "workflows").mkdir(parents=True)
    assert paths.config_root_with_source() == (pack, "vendored")

    # Nothing else: no repo root, no env -> error naming only those two.
    monkeypatch.delenv("REPO_ROOT", raising=False)
    with pytest.raises(paths.ConfigRootError) as exc:
        paths.config_root_with_source()
    msg = str(exc.value)
    assert "ORCHESTRATOR_CONFIG" in msg and ".orchestrator/<pack>/" in msg
    assert "config pull" in msg
    assert "~/.orchestrator/pack" not in msg
