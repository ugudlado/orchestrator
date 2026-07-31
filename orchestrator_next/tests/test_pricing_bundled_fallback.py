"""D4: pricing.py falls back to bundled_config_root()/pricing.yaml when the
resolved config root has none — so cost accounting survives a workflow-only
pack root (pricing.yaml stays engine-owned)."""
from __future__ import annotations

import datetime

import pytest

from orchestrator_next import pricing as _pricing_mod
from orchestrator_next.pricing import _lookup_price


@pytest.fixture(autouse=True)
def clear_pricing_cache():
    _pricing_mod._load_pricing_table.cache_clear()
    yield
    _pricing_mod._load_pricing_table.cache_clear()


def test_falls_back_to_bundled_pricing_when_config_root_has_none(tmp_path, monkeypatch):
    """config_root has no pricing.yaml -> falls back to the real bundled
    config/pricing.yaml (which prices claude-sonnet-4-6, per repo convention)."""
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    # bundled_config_root() is unpatched — real engine-bundled config/pricing.yaml.

    price = _lookup_price("claude-sonnet-4-6", datetime.datetime(2026, 7, 15))
    assert price is not None


def test_config_root_pricing_wins_over_bundled_when_present(tmp_path, monkeypatch):
    """A config root WITH its own pricing.yaml is used as-is — no fallback."""
    (tmp_path / "pricing.yaml").write_text(
        "models:\n"
        "  - model_id: custom-model-xyz\n"
        "    input_usd: 1.0\n"
        "    output_usd: 2.0\n"
        "    effective_from: \"2025-01-01T00:00:00\"\n"
    )
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)

    price = _lookup_price("custom-model-xyz", datetime.datetime(2026, 7, 15))
    assert price is not None
    assert price["input"] == 1.0

    # Bundled pricing.yaml's rows are NOT visible — config_root's file is used
    # exclusively, not merged with the bundled fallback.
    bundled_only = _lookup_price("claude-sonnet-4-6", datetime.datetime(2026, 7, 15))
    assert bundled_only is None


def test_neither_config_root_nor_bundled_has_pricing_yields_empty_table(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    monkeypatch.setattr("orchestrator_next.paths.engine_data_dir", lambda: tmp_path / "nonexistent")

    price = _lookup_price("claude-sonnet-4-6", datetime.datetime(2026, 7, 15))
    assert price is None
