"""Tests for the orchestrator models CLI verb."""
from __future__ import annotations

from pathlib import Path

import yaml


def _write_models(path: Path, models: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"models": models}))


def test_models_prints_attributed_table(monkeypatch, tmp_path, capsys):
    """AC-5: prints TIER, SUBPROCESS, MODEL_ID, SOURCE columns per tier."""
    from orchestrator_next.models_verb import main

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
    _write_models(home_models, {"opus": {"subprocess": "cursor"}})

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(config_root))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "TIER" in out and "SUBPROCESS" in out and "MODEL_ID" in out and "SOURCE" in out
    assert "opus" in out
    assert "sonnet" in out
    assert "config_root" in out or "config_root" in out


def test_models_source_shows_home_override(monkeypatch, tmp_path, capsys):
    """SOURCE column names the file/layer that supplied each tier."""
    from orchestrator_next.models_verb import main

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    home_models = home / ".orchestrator" / "models.yaml"

    _write_models(routes_yaml, {"opus": {"subprocess": "cursor", "model_id": "b"}})
    _write_models(home_models, {"opus": {"subprocess": "claude", "model_id": "a"}})

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(config_root))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "opus" in out
    assert "cursor" in out
    assert "config_root" in out


def test_models_renders_fallback_chain_with_active_marker(monkeypatch, tmp_path, capsys):
    """D3: `orchestrator models` prints the full chain per alias, marking
    which candidate is active on this machine."""
    from orchestrator_next.models_verb import main

    config_root = tmp_path / "config"
    home = tmp_path / "home"
    routes_yaml = config_root / "models.yaml"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude_bin = bin_dir / "claude"
    claude_bin.write_text("#!/bin/sh\nexit 0\n")
    claude_bin.chmod(0o755)

    routes_yaml.parent.mkdir(parents=True, exist_ok=True)
    routes_yaml.write_text(yaml.dump({
        "models": {"composer": [
            {"subprocess": "cursor", "model_id": "composer-2.5"},
            {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
        ]},
        "tools": {"cursor": {"binary": "cursor-agent"}, "claude": {"binary": "claude"}},
    }))

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(config_root))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(bin_dir))  # only claude present -> fallback #1
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Fallback chains:" in out
    assert "composer:" in out
    assert "[FALLBACK]" in out
    assert "* #1" in out  # candidate #1 (claude) is the active one


def test_models_missing_config_root_falls_back_to_bundled(monkeypatch, capsys, tmp_path):
    """Distribution improvements T1: ORCHESTRATOR_CONFIG unset no longer hard
    errors — it falls back to the bundled config, so `models` runs clean."""
    from orchestrator_next.models_verb import main
    from orchestrator_next.paths import bundled_config_root

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))  # no vendored config here

    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert bundled_config_root().is_dir()
    assert out  # prints the resolved tier table instead of erroring
