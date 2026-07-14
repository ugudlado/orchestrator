"""Tests for ~/.orchestrator/models.yaml layer in model route resolution."""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.model_routes import resolve_field


def _write_models(path: Path, models: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"models": models}))


def _setup_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: home)


def test_home_file_overrides_config_root(monkeypatch, tmp_path):
    """AC-1: home file sets opus.subprocess=cursor, no env vars → 'cursor'."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"}})
    _write_models(home_models, {"opus": {"subprocess": "cursor"}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "subprocess") == "cursor"


def test_precedence_env_file_over_home_over_config(monkeypatch, tmp_path):
    """AC-2: env-file > home > config-root."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env_file = tmp_path / "env_models.yaml"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"subprocess": "claude", "model_id": "a"}})
    _write_models(home_models, {"opus": {"subprocess": "cursor", "model_id": "b"}})
    _write_models(env_file, {"opus": {"subprocess": "codex", "model_id": "c"}})

    _setup_home(monkeypatch, home)
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(env_file))
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "subprocess") == "codex"
    assert resolve_field("opus", str(routes_yaml), "model_id") == "c"

    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    assert resolve_field("opus", str(routes_yaml), "subprocess") == "cursor"

    home_models.unlink()
    assert resolve_field("opus", str(routes_yaml), "subprocess") == "claude"


def test_home_partial_tier_falls_through(monkeypatch, tmp_path):
    """AC-3: home defines only sonnet → opus falls through to config-root."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(
        routes_yaml,
        {
            "opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"},
            "sonnet": {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
        },
    )
    _write_models(home_models, {"sonnet": {"subprocess": "cursor"}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "subprocess") == "claude"
    assert resolve_field("sonnet", str(routes_yaml), "subprocess") == "cursor"


def test_malformed_home_yaml_falls_through(monkeypatch, tmp_path):
    """AC-4: malformed home YAML → config-root value, no raise."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"}})
    home_models.parent.mkdir(parents=True, exist_ok=True)
    home_models.write_text('":\n  not: [yaml')

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "subprocess") == "claude"


def test_resolve_all_with_source_labels(monkeypatch, tmp_path):
    """resolve_all_with_source returns per-tier fields with source labels."""
    from orchestrator_next.model_routes import resolve_all_with_source

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env_file = tmp_path / "env_models.yaml"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"subprocess": "claude", "model_id": "a"}})
    _write_models(home_models, {"sonnet": {"subprocess": "cursor", "model_id": "b"}})
    _write_models(env_file, {"haiku": {"subprocess": "codex", "model_id": "c"}})

    _setup_home(monkeypatch, home)
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(env_file))
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    result = resolve_all_with_source(str(routes_yaml))

    assert set(result.keys()) >= {"opus", "sonnet", "haiku"}
    assert result["opus"]["subprocess"] == "claude"
    assert result["opus"]["subprocess_source"] == "config_root"
    assert result["sonnet"]["subprocess"] == "cursor"
    assert result["sonnet"]["subprocess_source"] == "user_home"
    assert result["haiku"]["subprocess"] == "codex"
    assert result["haiku"]["subprocess_source"] == "env_file"

    for tier in result.values():
        assert "subprocess" in tier
        assert "subprocess_source" in tier
        assert "model_id" in tier
        assert "model_id_source" in tier
