"""Tests for D4's doctor changes: loosened models.yaml presence check +
alias-resolves-to-a-route safety net for pack/agent-config independence."""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.doctor import (
    check_config_root,
    check_contract_aliases_resolve,
    check_models_layer_present,
)


def _write_models(
    path: Path,
    models: dict,
    tools: dict | None = None,
    step_models: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {"models": models}
    if tools is not None:
        data["tools"] = tools
    if step_models is not None:
        data["step_models"] = step_models
    path.write_text(yaml.dump(data))


def _write_contract(
    steps_root: Path,
    step_id: str,
    *,
    prompt: str | None = None,
    run: str | None = None,
) -> None:
    step_dir = steps_root / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    body: dict = {"id": step_id, "version": 1}
    if prompt:
        body["prompt"] = prompt
        (step_dir / Path(prompt).name).write_text("# stub skill\n")
    if run:
        body["run"] = run
        (step_dir / run).write_text("#!/bin/sh\nexit 0\n")
    (step_dir / "contract.yaml").write_text(yaml.dump(body))


# ---------------------------------------------------------------------------
# check_config_root: models.yaml is no longer part of the FAIL criteria
# ---------------------------------------------------------------------------

def test_config_root_passes_without_models_yaml(tmp_path):
    """A workflow-only pack root (no models.yaml of its own) must not FAIL
    check_config_root — routing can come from another layer entirely."""
    (tmp_path / "workflows").mkdir()
    (tmp_path / "steps").mkdir()
    result = check_config_root(tmp_path)
    assert result.status == "PASS"


def test_config_root_still_fails_without_workflows_or_steps(tmp_path):
    (tmp_path / "workflows").mkdir()
    # no steps/ dir
    result = check_config_root(tmp_path)
    assert result.status == "FAIL"


def test_models_layer_present_passes_with_config_root_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    _write_models(tmp_path / "models.yaml", {"opus": {"tool": "claude", "model_id": "x"}})
    result = check_models_layer_present(tmp_path)
    assert result.status == "PASS"


def test_models_layer_present_passes_with_home_file_only(tmp_path, monkeypatch):
    """Home layer alone is enough — workflow-only packs rely on this."""
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    _write_models(home / ".orchestrator" / "models.yaml", {"opus": {"tool": "claude", "model_id": "x"}})
    result = check_models_layer_present(tmp_path)  # tmp_path/models.yaml does not exist
    assert result.status == "PASS"


def test_models_layer_present_warns_when_no_layer_has_models(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    result = check_models_layer_present(tmp_path)
    assert result.status == "WARN"


# ---------------------------------------------------------------------------
# check_contract_aliases_resolve: step_models-driven
# ---------------------------------------------------------------------------

def test_contract_aliases_resolve_via_step_models(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    _write_models(
        tmp_path / "models.yaml",
        {"strong": {"tool": "claude", "model_id": "claude-opus-5"}},
        tools={"claude": {"binary": "claude"}},
        step_models={"my-step": "strong"},
    )
    _write_contract(tmp_path / "steps", "my-step", prompt="SKILL.md")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "PASS"


def test_contract_aliases_resolve_warns_on_missing_step_models(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")

    _write_models(tmp_path / "models.yaml", {"strong": {"tool": "claude", "model_id": "x"}})
    _write_contract(tmp_path / "steps", "my-step", prompt="SKILL.md")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "WARN"
    assert "my-step" in result.detail
    assert "step_models" in result.detail


def test_contract_aliases_resolve_warns_on_unrouted_alias(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")

    _write_models(
        tmp_path / "models.yaml",
        {"strong": {"tool": "claude", "model_id": "x"}},
        step_models={"my-step": "gpt5-turbo"},
    )
    _write_contract(tmp_path / "steps", "my-step", prompt="SKILL.md")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "WARN"
    assert "gpt5-turbo" in result.detail


def test_contract_aliases_resolve_warns_when_binary_missing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")  # claude not on PATH

    _write_models(
        tmp_path / "models.yaml",
        {"strong": {"tool": "claude", "model_id": "claude-opus-5"}},
        step_models={"my-step": "strong"},
    )
    _write_contract(tmp_path / "steps", "my-step", prompt="SKILL.md")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "WARN"
    assert "strong" in result.detail
    assert "claude" in result.detail
    assert "~/.orchestrator/models.yaml" in result.detail
    assert "tool" in result.detail
