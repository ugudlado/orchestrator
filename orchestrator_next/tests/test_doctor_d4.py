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


def _write_models(path: Path, models: dict, tools: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"models": models}
    if tools is not None:
        data["tools"] = tools
    path.write_text(yaml.dump(data))


def _write_contract(steps_root: Path, step_id: str, model: str | None = None, run: str | None = None) -> None:
    step_dir = steps_root / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    body: dict = {"id": step_id, "version": 1}
    if model:
        body["model"] = model
    if run:
        body["run"] = run
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
    assert "steps" in result.detail


# ---------------------------------------------------------------------------
# check_models_layer_present
# ---------------------------------------------------------------------------

def test_models_layer_present_passes_via_config_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    _write_models(tmp_path / "models.yaml", {"opus": {"subprocess": "claude", "model_id": "x"}})

    result = check_models_layer_present(tmp_path)
    assert result.status == "PASS"
    assert "config_root" in result.detail


def test_models_layer_present_passes_via_user_home_when_config_root_bare(tmp_path, monkeypatch):
    """A slim pack root with NO models.yaml still passes because the user's
    home file covers routing."""
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    _write_models(home / ".orchestrator" / "models.yaml", {"opus": {"subprocess": "claude", "model_id": "x"}})

    result = check_models_layer_present(tmp_path)  # tmp_path/models.yaml does not exist
    assert result.status == "PASS"
    assert "user_home" in result.detail


def test_models_layer_present_warns_when_no_layer_has_models(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    result = check_models_layer_present(tmp_path)
    assert result.status == "WARN"
    assert "config_root" in result.detail
    assert "user_home" in result.detail
    assert "env_file" in result.detail


# ---------------------------------------------------------------------------
# check_contract_aliases_resolve
# ---------------------------------------------------------------------------

def test_contract_aliases_resolve_passes_with_no_agent_steps(tmp_path):
    steps_root = tmp_path / "steps"
    _write_contract(steps_root, "script-step", run="script.sh")
    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "PASS"
    assert "0 agent-step aliases" in result.detail


def test_contract_aliases_resolve_passes_when_alias_available(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    _write_models(tmp_path / "models.yaml", {"opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"}},
                  tools={"claude": {"binary": "claude"}})
    _write_contract(tmp_path / "steps", "my-step", model="opus")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "PASS"


def test_contract_aliases_resolve_warns_on_unrouted_alias(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")

    # Contract references an alias no layer defines at all.
    _write_contract(tmp_path / "steps", "my-step", model="gpt5-turbo")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "WARN"
    assert "gpt5-turbo" in result.detail


def test_contract_aliases_resolve_warns_when_binary_missing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")  # claude not on PATH

    _write_models(tmp_path / "models.yaml", {"opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"}})
    _write_contract(tmp_path / "steps", "my-step", model="opus")

    result = check_contract_aliases_resolve(tmp_path)
    assert result.status == "WARN"
    assert "opus" in result.detail
