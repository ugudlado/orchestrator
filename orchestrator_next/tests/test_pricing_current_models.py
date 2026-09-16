"""Every currently-routed model id must resolve a price via pricing.py.

Sources: .tmp/e2e/.orchestrator/workflows/models.yaml `models:` block (the
model ids the pack actually routes to) and the alias->id resolution table
noted in orchestrator_next/pack_export.py's ALIAS_TO_CLAUDE_MODEL comment
(strong/standard/fast/code -> opus/sonnet/haiku aliases, which Claude Code
resolves to a concrete claude-* id at runtime — this test can't know that
mapping, so it asserts the ids actually declared in pricing.yaml resolve,
not the alias names themselves).
"""
from __future__ import annotations

import datetime

import pytest

from orchestrator_next.pricing import _lookup_price, _load_pricing_table


@pytest.fixture(autouse=True)
def clear_pricing_cache():
    _load_pricing_table.cache_clear()
    yield
    _load_pricing_table.cache_clear()


# Model ids referenced by .tmp/e2e/.orchestrator/workflows/models.yaml's
# `models:` block (the routes actually used to dispatch strong/standard/fast/
# code tiers) plus composer-2.5 for the `code` fallback chain.
ROUTED_MODEL_IDS = [
    "claude-opus-5",     # strong
    "claude-sonnet-5",   # standard
    "claude-haiku-4-5",  # fast
    "composer-2.5",      # code (cursor rung)
]


@pytest.mark.parametrize("model_id", ROUTED_MODEL_IDS)
def test_routed_model_id_has_a_price(model_id):
    """Every tier in models.yaml must resolve to a priced model_id.

    Mirrors the intent of test_every_routed_tier_has_a_price referenced in
    models.yaml's header comment: an unpriced routed model records no cost
    at all, which is the failure mode pricing.yaml is designed to avoid.
    """
    now = datetime.datetime(2026, 9, 17)
    result = _lookup_price(model_id, now)
    assert result is not None, f"{model_id!r} is routed in models.yaml but has no pricing.yaml row"
    assert result["input"] > 0 or model_id == "coder"


def test_dated_haiku_id_resolves_via_exact_row_not_suffix_stripping():
    """claude-haiku-4-5-20251001 has its own exact row in pricing.yaml.

    Note for the match-rule question: _lookup_price tries an exact model_id
    match first and only falls back to stripping a trailing -YYYYMMDD date
    suffix (via _DATED_MODEL_SUFFIX_RE) if the exact match misses. Since this
    id has both an exact row AND a bare claude-haiku-4-5 row, this test can't
    tell which path served it by rate alone (they're identical) — it only
    confirms resolution succeeds. See test below for the actual suffix-strip
    behavior in isolation.
    """
    now = datetime.datetime(2026, 9, 17)
    result = _lookup_price("claude-haiku-4-5-20251001", now)
    assert result is not None
    assert result["input"] == 0.8
    assert result["output"] == 4.0


def test_suffix_stripping_fallback_when_no_exact_dated_row(tmp_path, monkeypatch):
    """Confirms the -YYYYMMDD suffix-strip fallback in isolation.

    A dated id with NO exact row (unlike claude-haiku-4-5-20251001, which has
    both) must still resolve by falling back to its bare-id row.
    """
    pricing_yaml = tmp_path / "pricing.yaml"
    pricing_yaml.write_text(
        "models:\n"
        "  - model_id: claude-opus-4-5\n"
        "    input_usd: 15.0\n"
        "    output_usd: 75.0\n"
        "    cache_read_usd: 1.5\n"
        "    cache_creation_usd: 18.75\n"
        "    effective_from: \"2025-01-01T00:00:00\"\n"
    )
    monkeypatch.setattr("orchestrator_next.paths.config_root", lambda: tmp_path)
    now = datetime.datetime(2026, 9, 17)
    # No row for claude-opus-4-5-20251101 (the dated snapshot per claude-api
    # skill's shared/models.md legacy table) — must fall back to the bare id.
    result = _lookup_price("claude-opus-4-5-20251101", now)
    assert result is not None
    assert result["input"] == 15.0


def test_claude_opus_4_1_has_no_price_entry_by_design():
    """claude-opus-4-1 is NOT priced — documents a known gap, not a bug.

    The claude-api skill's shared/models.md lists claude-opus-4-1 (full id
    claude-opus-4-1-20250805) in its "Legacy Models" table as deprecated
    (retiring 2026-08-05, migrate to claude-opus-5) but states no per-token
    rate for it anywhere in the skill. Per the "don't guess" rule, no row was
    added. This test documents that a run recording usage.model =
    "claude-opus-4-1" will produce cost_usd = None (cost_partial), matching
    the incident this pricing update was meant to address for models that
    ARE priceable — this one remains an open gap until a rate is sourced.
    """
    now = datetime.datetime(2026, 9, 17)
    assert _lookup_price("claude-opus-4-1", now) is None
    assert _lookup_price("claude-opus-4-1-20250805", now) is None
