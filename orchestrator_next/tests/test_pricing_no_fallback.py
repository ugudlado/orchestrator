"""An unpriced model must produce NO cost, never a guessed one.

pricing.yaml used to carry a __default__ row (Opus rates: $15/$75 per MTok) that
any unmatched model_id silently fell through to. That turned an unknown cost into
a confident wrong one — a cursor "auto" step billed $8.33 against a true ~$1.15,
with nothing on screen saying the rate was invented. No cost beats a fake cost.
"""
from __future__ import annotations

import datetime as _dt

from orchestrator_next.pricing import _compute_cost_usd, _lookup_price

_NOW = _dt.datetime(2026, 7, 13)


def test_known_model_still_prices():
    price = _lookup_price("claude-haiku-4-5", _NOW)
    assert price is not None
    assert price["input"] == 0.8


def test_composer_is_priced():
    """cursor-agent is pinned to composer-2.5, so it must have a row."""
    price = _lookup_price("composer-2.5", _NOW)
    assert price is not None
    assert price["input"] == 0.5
    assert price["output"] == 2.5
    assert price["cache_read"] == 0.2


def test_unpriced_model_yields_no_price():
    assert _lookup_price("no-such-model-xyz", _NOW) is None


def test_unpriced_model_yields_no_cost_not_a_guess():
    usage = {"model": "no-such-model-xyz", "input_tokens": 100_000, "output_tokens": 10_000}
    model_id, cost = _compute_cost_usd("developer", usage, now=_NOW)
    assert model_id == "no-such-model-xyz"
    assert cost is None  # not a __default__-rate fabrication
