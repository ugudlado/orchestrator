"""Config-root and workflow-ref resolution.

The pack root is an *input* to the engine, never something it searches for:
``--config`` on ``start`` (which persists it on the run) or
``ORCHESTRATOR_CONFIG``. Multi-pack auto-discovery under ``.orchestrator/``
and ``<pack>/<workflow>`` qualification are gone with it.
"""
from pathlib import Path

import pytest

from orchestrator_next.paths import (
    DEFAULT_PACK,
    WorkflowRefError,
    config_root,
    config_root_with_source,
    list_workflows,
    resolve_workflow_ref,
)


def _pack(tmp_path: Path, *workflows: str) -> Path:
    root = tmp_path / "pack"
    (root / "workflows").mkdir(parents=True)
    for name in workflows:
        (root / "workflows" / f"{name}.yaml").write_text("steps: []\n")
    return root


def test_explicit_config_is_the_config_root(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", "/some/config")
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert config_root() == Path("/some/config")
    assert config_root_with_source() == (Path("/some/config"), "env")


def test_no_config_root_errors_with_a_hint(tmp_path, monkeypatch):
    """A repo laid out with .orchestrator/<pack>/ is NOT auto-discovered."""
    import orchestrator_next.paths as paths

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / ".orchestrator" / "mypack" / "workflows").mkdir(parents=True)
    with pytest.raises(paths.ConfigRootError) as exc:
        config_root()
    msg = str(exc.value)
    assert "--config" in msg and "ORCHESTRATOR_CONFIG" in msg


def test_bare_workflow_resolves_inside_the_config_root(tmp_path, monkeypatch):
    root = _pack(tmp_path, "feature")
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(root))
    assert resolve_workflow_ref("feature") == (DEFAULT_PACK, "feature", root)


def test_unknown_workflow_lists_what_is_available(tmp_path, monkeypatch):
    root = _pack(tmp_path, "feature", "bugfix")
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(root))
    with pytest.raises(WorkflowRefError, match="bugfix, feature"):
        resolve_workflow_ref("nope")


def test_pack_qualified_refs_are_rejected(tmp_path, monkeypatch):
    """`mypack/feature` was multi-pack disambiguation; there is one root now."""
    root = _pack(tmp_path, "feature")
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(root))
    with pytest.raises(WorkflowRefError, match="bare workflow name"):
        resolve_workflow_ref("mypack/feature")


def test_list_workflows_indexes_the_config_root(tmp_path, monkeypatch):
    root = _pack(tmp_path, "feature", "bugfix")
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(root))
    assert list_workflows() == {
        "bugfix": [(DEFAULT_PACK, root)],
        "feature": [(DEFAULT_PACK, root)],
    }


def test_list_workflows_is_empty_without_a_config_root(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert list_workflows() == {}
