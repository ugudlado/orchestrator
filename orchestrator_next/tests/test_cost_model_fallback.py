"""Cost accounting when the harness does not report which model answered.

`pricing._compute_cost_usd` keys its rate lookup on `usage.model`; with no
model it returns `(None, None)` and record stamps no cost at all. The Claude
mod reported only token counts, so every agent step summed to `cost_usd: 0.0`
despite real usage. Two defences, both here:

1. `protocol.done` falls back to the model id the dispatcher routed the step
   to, and marks the result `cost_partial`.
2. `record` keeps a model that has no pricing row, marking `cost_partial`
   rather than leaving a bare 0 that sums into a total reading as free.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from orchestrator_next import pricing as _pricing_mod
from orchestrator_next.record import _build_history_entry


@pytest.fixture(autouse=True)
def clear_pricing_cache():
    _pricing_mod._load_pricing_table.cache_clear()
    yield
    _pricing_mod._load_pricing_table.cache_clear()


@pytest.fixture
def priced(tmp_path, monkeypatch) -> Path:
    (tmp_path / "pricing.yaml").write_text(textwrap.dedent("""
        models:
          - model_id: claude-opus-4-1
            input_usd: 15.0
            output_usd: 75.0
            cache_read_usd: 1.5
            cache_creation_usd: 18.75
            effective_from: "2025-01-01T00:00:00"
    """))
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    return tmp_path


def _entry(usage: dict) -> dict:
    return _build_history_entry(
        {"usage": usage, "agent": "strong"},
        "design", "main", "completed", {}, "strong", {"step_history": []},
    )


def test_usage_without_a_model_records_no_cost(priced):
    """The bug as it shipped: tokens billed, cost silently zero."""
    entry = _entry({"input_tokens": 42, "output_tokens": 17840})
    assert entry["usage"].get("cost_usd") is None


def test_usage_with_a_model_is_priced(priced):
    entry = _entry({
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "model": "claude-opus-4-1",
    })
    assert entry["usage"]["cost_usd"] == pytest.approx(15.0)
    assert "cost_partial" not in entry["usage"]


def test_cache_tokens_are_billed_too(priced):
    """The mod dropped both cache counts, undercounting every step."""
    entry = _entry({
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "model": "claude-opus-4-1",
    })
    assert entry["usage"]["cost_usd"] == pytest.approx(1.5 + 18.75)


def test_unpriced_model_that_billed_tokens_is_marked_partial(priced):
    """No pricing row is not the same as free."""
    entry = _entry({
        "input_tokens": 42,
        "output_tokens": 17840,
        "model": "some-unpriced-model",
    })
    assert entry["usage"]["cost_partial"] is True
    assert entry["usage"]["model"] == "some-unpriced-model"
    assert entry["usage"].get("cost_usd") is None


def test_unpriced_model_with_no_tokens_is_not_marked_partial(priced):
    """An inline step that billed nothing is genuinely free, not unknown."""
    entry = _entry({"input_tokens": 0, "output_tokens": 0, "model": "some-unpriced-model"})
    assert "cost_partial" not in entry["usage"]
