"""Tests for --models-config CLI override."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orchestrator_next.models_config_cli import (
    apply_models_config,
    extract_models_config_args,
)
from orchestrator_next.model_routes import resolve_field, resolve_step_alias


def test_extract_models_config_flag():
    remaining, path = extract_models_config_args(
        ["ORC-1", "--models-config", "/tmp/m.yaml", "--schema", "feature"]
    )
    assert path == "/tmp/m.yaml"
    assert remaining == ["ORC-1", "--schema", "feature"]


def test_extract_kv_form():
    remaining, path = extract_models_config_args(
        ["tkt", "models.config=/tmp/m.yaml"]
    )
    assert path == "/tmp/m.yaml"
    assert remaining == ["tkt"]


def test_apply_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit) as ei:
        apply_models_config(str(tmp_path / "nope.yaml"))
    assert ei.value.code == 7


def test_consume_sets_env_and_overrides_step_models(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "models.yaml").write_text(
        yaml.dump(
            {
                "models": {
                    "standard": {"tool": "claude", "model_id": "base-id"},
                    "strong": {"tool": "claude", "model_id": "strong-id"},
                },
                "step_models": {"implement": "standard"},
            }
        )
    )

    override = tmp_path / "override.yaml"
    override.write_text(
        yaml.dump(
            {
                "models": {
                    "code": {"tool": "cursor", "model_id": "composer-2.5"},
                },
                "step_models": {"implement": "code"},
            }
        )
    )

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    remaining, path = extract_models_config_args(
        ["state.yaml", "--models-config", str(override)]
    )
    assert remaining == ["state.yaml"]
    assert path == str(override)
    # Use monkeypatch so ORCHESTRATOR_MODELS_CONFIG is restored after the test.
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(override.resolve()))

    assert resolve_step_alias("implement", None, str(config_root / "models.yaml")) == "code"
    assert resolve_field("code", str(config_root / "models.yaml"), "model_id") == "composer-2.5"
    # Unmentioned tiers still fall through to config-root.
    assert resolve_field("standard", str(config_root / "models.yaml"), "model_id") == "base-id"
