"""Tests for D3's doctor guard rail: WARN when any alias resolves to a
non-first candidate in its fallback chain."""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.doctor import check_no_silent_fallback, check_tools_available


def _write_models(path: Path, models: dict, tools: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"models": models}
    if tools is not None:
        data["tools"] = tools
    path.write_text(yaml.dump(data))


def test_no_fallback_passes_for_scalar_routes(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    home = tmp_path / "home"  # isolate from the real ~/.orchestrator/models.yaml
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)
    _write_models(config_root / "models.yaml", {
        "opus": {"tool": "claude", "model_id": "claude-opus-4-7"},
    })
    result = check_no_silent_fallback(config_root)
    assert result.status == "PASS"


def test_warns_when_alias_on_fallback_candidate(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    home = tmp_path / "home"  # isolate from the real ~/.orchestrator/models.yaml
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude_bin = bin_dir / "claude"
    claude_bin.write_text("#!/bin/sh\nexit 0\n")
    claude_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))  # only claude present, not cursor-agent

    _write_models(
        config_root / "models.yaml",
        {"composer": [
            {"tool": "cursor", "model_id": "composer-2.5"},
            {"tool": "claude", "model_id": "claude-sonnet-4-6"},
        ]},
        tools={"cursor": {"binary": "cursor-agent"}, "claude": {"binary": "claude"}},
    )

    result = check_no_silent_fallback(config_root)
    assert result.status == "WARN"
    assert "composer" in result.detail


def test_check_tools_available_handles_chain_aliases(monkeypatch, tmp_path):
    """RULE 3 must not silently drop list-shaped (chain) aliases from the
    PATH check — every subprocess named anywhere in a chain should count."""
    config_root = tmp_path / "config"
    # check_tools_available reads config_root/models.yaml directly (not
    # layered), so no Path.home() isolation is needed here — but keep PATH clean.
    monkeypatch.setenv("PATH", "/nonexistent-empty-dir")
    _write_models(config_root / "models.yaml", {"composer": [
        {"tool": "cursor", "model_id": "composer-2.5"},
        {"tool": "claude", "model_id": "claude-sonnet-4-6"},
    ]})

    result = check_tools_available(config_root)
    assert result.status == "WARN"
    assert "cursor" in result.detail
    assert "claude" in result.detail
