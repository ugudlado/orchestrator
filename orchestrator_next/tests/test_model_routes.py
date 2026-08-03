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


def test_repo_config_root_overrides_home(monkeypatch, tmp_path):
    """Repo→global: a non-bundled (vendored/explicit) config root outranks
    the home file — home's opus entry loses to the repo's."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"tool": "claude", "model_id": "claude-opus-4-7"}})
    _write_models(home_models, {"opus": {"tool": "cursor"}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "tool") == "claude"


def test_home_overrides_pack_floor(monkeypatch, tmp_path):
    """Repo→global floor: when the config root IS the downloaded pack (or dev
    checkout), the home file outranks it — global defaults are only the floor."""
    import orchestrator_next.paths as paths

    home = tmp_path / "home"
    home_models = home / ".orchestrator" / "models.yaml"
    pack = tmp_path / "pack"
    pack_yaml = pack / "config" / "models.yaml"
    _write_models(pack_yaml, {"opus": {"tool": "claude", "model_id": "floor"}})
    _write_models(home_models, {"opus": {"tool": "cursor", "model_id": "x"}})
    monkeypatch.setattr(paths, "pack_root", lambda: pack)

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(pack_yaml), "tool") == "cursor"


def test_precedence_env_file_over_config_over_home(monkeypatch, tmp_path):
    """Test-only env-file layer > repo config-root > home."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env_file = tmp_path / "env_models.yaml"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"tool": "claude", "model_id": "a"}})
    _write_models(home_models, {"opus": {"tool": "cursor", "model_id": "b"}})
    _write_models(env_file, {"opus": {"tool": "codex", "model_id": "c"}})

    _setup_home(monkeypatch, home)
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(env_file))
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "tool") == "codex"
    assert resolve_field("opus", str(routes_yaml), "model_id") == "c"

    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    assert resolve_field("opus", str(routes_yaml), "tool") == "claude"

    routes_yaml.unlink()
    assert resolve_field("opus", str(routes_yaml), "tool") == "cursor"


def test_config_partial_tier_falls_through(monkeypatch, tmp_path):
    """AC-3: repo config defines only sonnet → opus falls through to home
    (per-ALIAS fallthrough — an alias undefined in a higher layer defers to
    the lower layer entirely). This is distinct from D3's wholesale-wins rule,
    which governs what happens when the SAME alias is named in two layers."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"sonnet": {"tool": "claude", "model_id": "claude-sonnet-4-6"}})
    _write_models(
        home_models,
        {
            "opus": {"tool": "cursor", "model_id": "composer-2.5"},
            "sonnet": {"tool": "cursor"},
        },
    )

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "tool") == "cursor"
    assert resolve_field("sonnet", str(routes_yaml), "tool") == "claude"


def test_wholesale_wins_no_partial_field_merge_within_same_alias(monkeypatch, tmp_path):
    """D3 regression pin: BACK-COMPAT DELTA (intentional). Before D3, home
    setting only `sonnet.tool` would inherit `model_id` from config_root
    via dict.update() accumulation. D3 makes the highest layer that names an
    alias own it WHOLESALE — home's partial {tool: cursor} entry for
    `sonnet` no longer inherits model_id from a lower layer; model_id resolves
    empty because the winning layer didn't state it. The higher layer must
    state the full route now. (Winning layer here is the repo config root,
    per the repo→global order.)"""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"sonnet": {"tool": "cursor"}})  # no model_id
    _write_models(
        home_models,
        {"sonnet": {"tool": "claude", "model_id": "claude-sonnet-4-6"}},
    )

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("sonnet", str(routes_yaml), "tool") == "cursor"
    # THE BREAK: model_id does NOT fall back to home's "claude-sonnet-4-6".
    assert resolve_field("sonnet", str(routes_yaml), "model_id") == ""


def test_malformed_config_yaml_falls_through(monkeypatch, tmp_path):
    """AC-4: malformed YAML in the winning (repo) layer → home value, no raise."""
    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    routes_yaml.parent.mkdir(parents=True, exist_ok=True)
    routes_yaml.write_text('":\n  not: [yaml')
    _write_models(home_models, {"opus": {"tool": "claude", "model_id": "claude-opus-4-7"}})

    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_field("opus", str(routes_yaml), "tool") == "claude"


def test_resolve_all_with_source_labels(monkeypatch, tmp_path):
    """resolve_all_with_source returns per-tier fields with source labels."""
    from orchestrator_next.model_routes import resolve_all_with_source

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env_file = tmp_path / "env_models.yaml"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"tool": "claude", "model_id": "a"}})
    _write_models(home_models, {"sonnet": {"tool": "cursor", "model_id": "b"}})
    _write_models(env_file, {"haiku": {"tool": "codex", "model_id": "c"}})

    _setup_home(monkeypatch, home)
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(env_file))
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    result = resolve_all_with_source(str(routes_yaml))

    assert set(result.keys()) >= {"opus", "sonnet", "haiku"}
    assert result["opus"]["tool"] == "claude"
    assert result["opus"]["tool_source"] == "config_root"
    assert result["sonnet"]["tool"] == "cursor"
    assert result["sonnet"]["tool_source"] == "user_home"
    assert result["haiku"]["tool"] == "codex"
    assert result["haiku"]["tool_source"] == "env_file"

    for tier in result.values():
        assert "tool" in tier
        assert "tool_source" in tier
        assert "model_id" in tier
        assert "model_id_source" in tier


def test_step_models_overrides_contract_alias(monkeypatch, tmp_path):
    """step_models:<step_id> wins over the contract's model: alias."""
    from orchestrator_next.model_routes import resolve_step_alias

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    routes_yaml.parent.mkdir(parents=True)
    routes_yaml.write_text(
        yaml.dump(
            {
                "models": {
                    "strong": {"tool": "claude", "model_id": "claude-opus-4-7"},
                    "standard": {"tool": "claude", "model_id": "claude-sonnet-4-6"},
                },
                "step_models": {"design-review": "strong"},
            }
        )
    )
    _setup_home(monkeypatch, home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_step_alias("design-review", "standard", str(routes_yaml)) == "strong"
    assert resolve_step_alias("explore", "standard", str(routes_yaml)) == "standard"


def test_step_models_per_step_fallthrough(monkeypatch, tmp_path):
    """Higher layer can override one step without wiping lower step_models."""
    from orchestrator_next.model_routes import resolve_step_alias

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    env_file = tmp_path / "env_models.yaml"
    routes_yaml = config_root / "models.yaml"
    routes_yaml.parent.mkdir(parents=True)
    routes_yaml.write_text(
        yaml.dump({"step_models": {"design": "strong", "implement": "code"}})
    )
    env_file.write_text(yaml.dump({"step_models": {"implement": "standard"}}))

    _setup_home(monkeypatch, home)
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(env_file))
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert resolve_step_alias("implement", "code", str(routes_yaml)) == "standard"
    assert resolve_step_alias("design", "strong", str(routes_yaml)) == "strong"
