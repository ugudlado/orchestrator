"""Tests for D3: runtime fallback chains.

An alias's `models:` entry may be a scalar route dict (today's shape) or a
list of candidate routes, tried in order — first candidate whose subprocess's
binary is on PATH wins. Covers: chain resolution, wholesale-wins across layer
combinations (list-over-scalar, scalar-over-list, list-over-list), back-compat
single-layer scalar, and exit-4 when a chain is fully exhausted.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orchestrator_next.model_routes import resolve_field, resolve_route


def _write_models(path: Path, models: dict, tools: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"models": models}
    if tools is not None:
        data["tools"] = tools
    path.write_text(yaml.dump(data))


def _setup_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: home)


def _no_env_overrides(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)


@pytest.fixture
def real_binary(monkeypatch):
    """A binary name guaranteed to resolve via shutil.which (no PATH-tool
    dependency in tests): a fake executable script on a scratch PATH dir."""
    def _make(tmp_path: Path, name: str) -> None:
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir(exist_ok=True)
        f = bin_dir / name
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ.get('PATH', '')}")
    return _make


def test_chain_picks_first_available_candidate(monkeypatch, tmp_path, real_binary):
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    real_binary(tmp_path, "cursor-agent")  # only "cursor" subprocess's binary present
    _write_models(
        routes_yaml,
        {"composer": [
            {"subprocess": "cursor", "model_id": "composer-2.5"},
            {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
        ]},
        tools={"cursor": {"binary": "cursor-agent"}, "claude": {"binary": "claude"}},
    )
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == "cursor"
    assert route["model_id"] == "composer-2.5"
    assert route["active_index"] == 0
    assert route["is_fallback"] is False
    assert route["num_candidates"] == 2


def test_chain_falls_back_to_second_candidate_when_first_binary_absent(monkeypatch, tmp_path, real_binary):
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    # Only "claude" binary present; "cursor" (first candidate) is absent.
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")
    real_binary(tmp_path, "claude")
    _write_models(
        routes_yaml,
        {"composer": [
            {"subprocess": "cursor", "model_id": "composer-2.5"},
            {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
        ]},
        tools={"cursor": {"binary": "cursor-agent"}, "claude": {"binary": "claude"}},
    )
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == "claude"
    assert route["model_id"] == "claude-sonnet-4-6"
    assert route["active_index"] == 1
    assert route["is_fallback"] is True


def test_chain_exhausted_yields_empty_route(monkeypatch, tmp_path):
    """No candidate's binary on PATH → subprocess/model_id come back empty,
    so run_loop's caller raises exit 4 (existing no-route error)."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")
    _write_models(
        routes_yaml,
        {"composer": [
            {"subprocess": "cursor", "model_id": "composer-2.5"},
            {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
        ]},
        tools={"cursor": {"binary": "cursor-agent"}, "claude": {"binary": "claude"}},
    )
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == ""
    assert route["model_id"] == ""


def test_run_loop_raises_exit_4_when_chain_exhausted(monkeypatch, tmp_path):
    from orchestrator_next.run_loop import run_agent_step

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")
    _write_models(routes_yaml, {"composer": [
        {"subprocess": "cursor", "model_id": "composer-2.5"},
    ]}, tools={"cursor": {"binary": "cursor-agent"}})
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    action = {"model": "composer", "step_id": "s1", "phase": "main", "attempt": 1}
    with pytest.raises(SystemExit) as exc_info:
        run_agent_step(
            action, repo_root=str(tmp_path), models_yaml=str(routes_yaml),
            state_raw={}, state_yaml_path=str(tmp_path / "state.yaml"), tmp_dir=tmp_path,
        )
    assert exc_info.value.code == 4


def test_scalar_route_not_path_gated_back_compat(monkeypatch, tmp_path):
    """A scalar route dispatches even if its binary is absent from PATH — it
    is NOT PATH-gated (only chains are). Preserves pre-D3 behavior: failure
    surfaces at invoke time, not as a resolver-level no-route error."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")
    _write_models(routes_yaml, {"opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"}})
    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("opus", str(routes_yaml))
    assert route["subprocess"] == "claude"
    assert route["model_id"] == "claude-opus-4-7"
    assert route["active_index"] == 0
    assert route["is_fallback"] is False
    assert resolve_field("opus", str(routes_yaml), "subprocess") == "claude"


def test_wholesale_wins_list_over_scalar(monkeypatch, tmp_path, real_binary):
    """Home defines a chain for an alias that config_root defines as scalar —
    home (list) wins wholesale; config_root's scalar is fully ignored."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    real_binary(tmp_path, "cursor-agent")
    _write_models(routes_yaml, {"composer": {"subprocess": "claude", "model_id": "claude-opus-4-7"}},
                  tools={"claude": {"binary": "claude"}})
    _write_models(home_models, {"composer": [
        {"subprocess": "cursor", "model_id": "composer-2.5"},
    ]}, tools={"cursor": {"binary": "cursor-agent"}})

    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == "cursor"
    assert route["model_id"] == "composer-2.5"
    assert route["num_candidates"] == 1  # home's chain has exactly 1 candidate


def test_wholesale_wins_scalar_over_list(monkeypatch, tmp_path):
    """Home defines a scalar for an alias that config_root defines as a
    chain — home (scalar) wins wholesale; config_root's chain fully ignored."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"composer": [
        {"subprocess": "cursor", "model_id": "composer-2.5"},
        {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
    ]})
    _write_models(home_models, {"composer": {"subprocess": "codex", "model_id": "gpt-5-codex"}})

    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == "codex"
    assert route["model_id"] == "gpt-5-codex"
    assert route["num_candidates"] == 1
    assert route["is_fallback"] is False


def test_wholesale_wins_list_over_list(monkeypatch, tmp_path, real_binary):
    """Home's chain fully replaces config_root's chain for the same alias —
    no element-wise merge across the two chains."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    real_binary(tmp_path, "codex")
    _write_models(routes_yaml, {"composer": [
        {"subprocess": "cursor", "model_id": "composer-2.5"},
    ]})
    _write_models(home_models, {"composer": [
        {"subprocess": "codex", "model_id": "gpt-5-codex"},
    ]}, tools={"codex": {"binary": "codex"}})

    _setup_home(monkeypatch, home)
    _no_env_overrides(monkeypatch)

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == "codex"
    assert route["model_id"] == "gpt-5-codex"


def test_env_override_layers_on_top_of_selected_candidate(monkeypatch, tmp_path, real_binary):
    """ORCHESTRATOR_MODEL_ROUTE_OVERRIDES is a separate, higher-precedence
    field-level override — NOT part of the wholesale-wins file-layer rule.
    A partial override (model_id only) still works, inheriting subprocess
    from whichever candidate PATH-selected."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"

    real_binary(tmp_path, "cursor-agent")
    _write_models(routes_yaml, {"composer": [
        {"subprocess": "cursor", "model_id": "composer-2.5"},
    ]}, tools={"cursor": {"binary": "cursor-agent"}})
    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", '{"composer": {"model_id": "composer-override"}}')

    route = resolve_route("composer", str(routes_yaml))
    assert route["subprocess"] == "cursor"  # inherited from selected candidate
    assert route["model_id"] == "composer-override"  # env override wins the field
