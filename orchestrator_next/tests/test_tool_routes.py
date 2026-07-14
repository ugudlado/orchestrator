"""Tests for D1: layering the `tools:` block through the same layer chain
(config_root > user_home > env_file) used by `models:` resolution."""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.model_routes import resolve_tool_template


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def _setup_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: home)


def test_tool_template_from_config_root(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    _write(routes_yaml, {"tools": {"claude": {"binary": "claude", "args_template": ["-p", "{prompt}"]}}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    binary, template = resolve_tool_template("claude", str(routes_yaml))
    assert binary == "claude"
    assert template == ["-p", "{prompt}"]


def test_user_home_tool_wins_wholesale_over_config_root(monkeypatch, tmp_path):
    """Highest layer that names a tool owns the whole entry — lower layers'
    fields for that tool are fully ignored, not merged in."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write(routes_yaml, {"tools": {"cursor": {
        "binary": "cursor-agent",
        "args_template": ["-p", "--model", "{model_id}", "{prompt}"],
    }}})
    # Home overrides binary only — args_template is NOT inherited from config_root.
    _write(home_models, {"tools": {"cursor": {"binary": "/opt/homebrew/bin/cursor-agent"}}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    binary, template = resolve_tool_template("cursor", str(routes_yaml))
    assert binary == "/opt/homebrew/bin/cursor-agent"
    assert template == []  # not inherited from config_root — wholesale-wins


def test_tool_not_in_any_layer_falls_back_to_bare_name(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    _write(routes_yaml, {"tools": {"claude": {"binary": "claude"}}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)

    binary, template = resolve_tool_template("codex", str(routes_yaml))
    assert binary == "codex"
    assert template == []


def test_env_file_tool_wins_over_home_and_config_root(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env_file = tmp_path / "env_models.yaml"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write(routes_yaml, {"tools": {"pi": {"binary": "pi-config"}}})
    _write(home_models, {"tools": {"pi": {"binary": "pi-home"}}})
    _write(env_file, {"tools": {"pi": {"binary": "pi-env"}}})

    _setup_home(monkeypatch, home)
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(env_file))

    binary, _ = resolve_tool_template("pi", str(routes_yaml))
    assert binary == "pi-env"
