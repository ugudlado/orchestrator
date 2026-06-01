"""Tests for file-based pricing lookup (config/pricing.yaml)."""
from __future__ import annotations

import datetime
import sys
import textwrap
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from orchestrator_next import pricing as _pricing_mod
from orchestrator_next.pricing import _lookup_price, _compute_cost_usd, _load_pricing_table


@pytest.fixture(autouse=True)
def clear_pricing_cache():
    _pricing_mod._load_pricing_table.cache_clear()
    yield
    _pricing_mod._load_pricing_table.cache_clear()


def _make_pricing_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "pricing.yaml"
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# _lookup_price
# ---------------------------------------------------------------------------

def test_exact_model_hit(tmp_path, monkeypatch):
    _make_pricing_yaml(tmp_path, """
        models:
          - model_id: claude-sonnet-4-6
            input_usd: 3.0
            output_usd: 15.0
            cache_read_usd: 0.3
            cache_creation_usd: 3.75
            effective_from: "2025-01-01T00:00:00"
    """)
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    now = datetime.datetime(2026, 1, 1)
    result = _lookup_price("claude-sonnet-4-6", now)
    assert result is not None
    assert result["input"] == 3.0
    assert result["output"] == 15.0


def test_default_fallback(tmp_path, monkeypatch):
    _make_pricing_yaml(tmp_path, """
        models:
          - model_id: __default__
            input_usd: 15.0
            output_usd: 75.0
            cache_read_usd: 1.5
            cache_creation_usd: 18.75
            effective_from: "2025-01-01T00:00:00"
    """)
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    now = datetime.datetime(2026, 1, 1)
    result = _lookup_price("unknown-model", now)
    assert result is not None
    assert result["input"] == 15.0


def test_effective_from_selects_latest_row(tmp_path, monkeypatch):
    _make_pricing_yaml(tmp_path, """
        models:
          - model_id: test-model
            input_usd: 1.0
            output_usd: 5.0
            cache_read_usd: 0.1
            cache_creation_usd: 1.0
            effective_from: "2025-01-01T00:00:00"
          - model_id: test-model
            input_usd: 2.0
            output_usd: 10.0
            cache_read_usd: 0.2
            cache_creation_usd: 2.0
            effective_from: "2026-01-01T00:00:00"
    """)
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    # Before second row is effective — returns first row
    result = _lookup_price("test-model", datetime.datetime(2025, 6, 1))
    assert result["input"] == 1.0
    # After second row is effective — returns second row
    result2 = _lookup_price("test-model", datetime.datetime(2026, 6, 1))
    assert result2["input"] == 2.0


def test_no_match_returns_none_with_warning(tmp_path, monkeypatch, capsys):
    _make_pricing_yaml(tmp_path, """
        models:
          - model_id: other-model
            input_usd: 1.0
            output_usd: 5.0
            cache_read_usd: 0.1
            cache_creation_usd: 1.0
            effective_from: "2025-01-01T00:00:00"
    """)
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    result = _lookup_price("no-such-model", datetime.datetime(2026, 1, 1))
    assert result is None
    captured = capsys.readouterr()
    assert "no price entry" in captured.err


def test_missing_pricing_yaml_returns_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    result = _lookup_price("any-model", datetime.datetime(2026, 1, 1))
    assert result is None
    captured = capsys.readouterr()
    assert "pricing.yaml not found" in captured.err


# ---------------------------------------------------------------------------
# _compute_cost_usd
# ---------------------------------------------------------------------------

def test_compute_cost_usd_with_usage(tmp_path, monkeypatch):
    _make_pricing_yaml(tmp_path, """
        models:
          - model_id: __default__
            input_usd: 3.0
            output_usd: 15.0
            cache_read_usd: 0.3
            cache_creation_usd: 3.75
            effective_from: "2025-01-01T00:00:00"
    """)
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "model": "__default__"}
    model_id, cost = _compute_cost_usd("some-agent", usage)
    assert model_id == "__default__"
    assert cost == pytest.approx(3.0)


def test_compute_cost_usd_no_tokens(tmp_path, monkeypatch):
    _make_pricing_yaml(tmp_path, """
        models:
          - model_id: __default__
            input_usd: 3.0
            output_usd: 15.0
            cache_read_usd: 0.3
            cache_creation_usd: 3.75
            effective_from: "2025-01-01T00:00:00"
    """)
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    usage: dict = {}
    model_id, cost = _compute_cost_usd("unknown-agent", usage)
    assert model_id is None
    assert cost is None
