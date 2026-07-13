"""Tests for doctor model route source check."""
from __future__ import annotations

import yaml

from orchestrator_next.doctor import check_model_route_sources, run_all


def _write_models(path, models: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"models": models}))


def test_check_model_route_sources_passes_with_detail(tmp_path):
    config_root = tmp_path / "config"
    _write_models(
        config_root / "models.yaml",
        {
            "opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"},
            "sonnet": {"subprocess": "claude", "model_id": "claude-sonnet-4-6"},
        },
    )

    result = check_model_route_sources(config_root)

    assert result.name == "model route sources"
    assert result.status == "PASS"
    assert "opus" in result.detail
    assert "sonnet" in result.detail
    assert "config_root" in result.detail


def test_run_all_includes_model_route_sources_check(monkeypatch, tmp_path, capsys):
    config_root = tmp_path / "config"
    _write_models(
        config_root / "models.yaml",
        {"opus": {"subprocess": "claude", "model_id": "claude-opus-4-7"}},
    )
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(config_root))
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

    rc = run_all([])
    out = capsys.readouterr().out

    assert rc in (0, 2)
    assert "model route sources" in out
    assert "PASS" in out
