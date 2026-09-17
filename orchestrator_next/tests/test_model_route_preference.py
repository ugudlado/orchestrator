"""An alias's `models:` entry may be a scalar route or an ordered preference list.

The engine no longer spawns vendor binaries, so nothing is PATH-probed: the
first entry of a list wins and the rest only document the author's fallback
order. What is still load-bearing is the wholesale-wins layering rule — the
highest file layer that names an alias owns it entirely, list or scalar, with
no cross-layer field or element merging.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.model_routes import resolve_field, resolve_route


def _write_models(path: Path, models: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"models": models}))


def _setup_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: home)


def _no_env_overrides(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)


def test_preference_list_picks_the_first_entry(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    routes_yaml = config_root / "models.yaml"
    _write_models(routes_yaml, {"opus": [
        {"model_id": "claude-opus-5"},
        {"model_id": "claude-sonnet-5"},
    ]})
    _setup_home(monkeypatch, tmp_path / "home")
    _no_env_overrides(monkeypatch)

    route = resolve_route("opus", str(routes_yaml))
    assert route["model_id"] == "claude-opus-5"
    assert route["active_index"] == 0
    assert route["num_candidates"] == 2
    assert route["is_fallback"] is False
    assert resolve_field("opus", str(routes_yaml), "model_id") == "claude-opus-5"


def test_scalar_route_resolves(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    routes_yaml = config_root / "models.yaml"
    _write_models(routes_yaml, {"opus": {"model_id": "claude-opus-5"}})
    _setup_home(monkeypatch, tmp_path / "home")
    _no_env_overrides(monkeypatch)

    route = resolve_route("opus", str(routes_yaml))
    assert route["model_id"] == "claude-opus-5"
    assert route["num_candidates"] == 1


def test_unknown_alias_yields_empty_route(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    routes_yaml = config_root / "models.yaml"
    _write_models(routes_yaml, {"opus": {"model_id": "claude-opus-5"}})
    _setup_home(monkeypatch, tmp_path / "home")
    _no_env_overrides(monkeypatch)

    assert resolve_route("nope", str(routes_yaml))["model_id"] == ""


def test_empty_list_yields_empty_route(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    routes_yaml = config_root / "models.yaml"
    _write_models(routes_yaml, {"opus": []})
    _setup_home(monkeypatch, tmp_path / "home")
    _no_env_overrides(monkeypatch)

    route = resolve_route("opus", str(routes_yaml))
    assert route["model_id"] == ""
    assert route["num_candidates"] == 0


def test_wholesale_wins_list_over_scalar(monkeypatch, tmp_path):
    """config_root's list wins entirely; home's scalar is fully ignored."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    _write_models(routes_yaml, {"composer": [{"model_id": "composer-2.5"}]})
    _write_models(home / ".orchestrator" / "models.yaml",
                  {"composer": {"model_id": "claude-opus-5"}})
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["model_id"] == "composer-2.5"
    assert route["num_candidates"] == 1


def test_wholesale_wins_scalar_over_list(monkeypatch, tmp_path):
    """config_root's scalar wins entirely; home's list is fully ignored."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    _write_models(routes_yaml, {"composer": {"model_id": "gpt-5-codex"}})
    _write_models(home / ".orchestrator" / "models.yaml", {"composer": [
        {"model_id": "composer-2.5"},
        {"model_id": "claude-sonnet-5"},
    ]})
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["model_id"] == "gpt-5-codex"
    assert route["num_candidates"] == 1


def test_wholesale_wins_list_over_list(monkeypatch, tmp_path):
    """No element-wise merge across two layers' lists."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    _write_models(routes_yaml, {"composer": [{"model_id": "gpt-5-codex"}]})
    _write_models(home / ".orchestrator" / "models.yaml",
                  {"composer": [{"model_id": "composer-2.5"}]})
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    assert resolve_route("composer", str(routes_yaml))["model_id"] == "gpt-5-codex"


def test_env_override_layers_on_top_of_selected_candidate(monkeypatch, tmp_path):
    """ORCHESTRATOR_MODEL_ROUTE_OVERRIDES is a separate, higher-precedence
    field-level override — not part of the wholesale-wins file-layer rule."""
    config_root = tmp_path / "config"
    routes_yaml = config_root / "models.yaml"

    _write_models(routes_yaml, {"composer": [{"model_id": "composer-2.5"}]})
    _setup_home(monkeypatch, tmp_path / "home")
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv(
        "ORCHESTRATOR_MODEL_ROUTE_OVERRIDES",
        '{"composer": {"model_id": "composer-override"}}',
    )

    route = resolve_route("composer", str(routes_yaml))
    assert route["model_id"] == "composer-override"
    assert route["source"] == "$ORCHESTRATOR_MODEL_ROUTE_OVERRIDES"
