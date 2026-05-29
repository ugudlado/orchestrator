"""T-1 RED: regression tests for dated-model pricing lookup (ORC-30).

Four scenarios:
  1. Dated ID (not seeded) falls back to base model pricing, NOT __default__.
  2. Exact dated match wins over base-strip fallback.
  3. Non-dated unknown model falls back to __default__.
  4. Dated ID with unseeded base falls back to __default__.

Tests 1 and 4 MUST fail on main before the fix (T-2) is applied.
Tests 2 and 3 may already pass and guard against T-2 regressions.
"""
from __future__ import annotations

import datetime
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import duckdb
import pytest

import orchestrator_next.record as _record_mod
from orchestrator_next.record import _lookup_price  # noqa: E402
from orchestrator_next.upsert import ensure_schema   # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_pricing_cache():
    """Clear the module-level _pricing_cache before/after each test.

    _pricing_cache is keyed by id(db). CPython can reuse the same memory
    address for a new in-memory connection after a prior one is GC'd, which
    would return stale rows. Clearing prevents this latent flake.
    """
    _record_mod._pricing_cache.clear()
    yield
    _record_mod._pricing_cache.clear()


@pytest.fixture()
def bare_db():
    """In-memory DuckDB with full schema but NO seeded pricing rows.

    Uses ensure_schema (runs migrations) then deletes all pricing rows so
    individual tests can seed exactly the rows they need.
    """
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    db.execute("DELETE FROM pricing")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EPOCH = datetime.datetime(2025, 1, 1, 0, 0, 0)
_NOW   = datetime.datetime(2026, 5, 10, 12, 0, 0)


def _insert(db, model_id: str, input_usd: float, output_usd: float = 0.0,
            cache_read: float = 0.0, cache_creation: float = 0.0):
    db.execute(
        "INSERT INTO pricing "
        "(model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, "
        "is_local, effective_from) VALUES (?, ?, ?, ?, ?, FALSE, ?)",
        [model_id, input_usd, output_usd, cache_read, cache_creation, _EPOCH],
    )


# ---------------------------------------------------------------------------
# Test 1 — AC-1: dated ID falls back to base model, not __default__
# ---------------------------------------------------------------------------

def test_dated_id_falls_back_to_base_model_pricing(bare_db):
    """_lookup_price for a dated variant not in DB returns the base-model rate.

    Seed:    claude-sonnet-4-6  (input=3.0)
             __default__        (input=15.0)
    Query:   claude-sonnet-4-6-20260315
    Expect:  input=3.0  (sonnet), NOT 15.0 (__default__)
    """
    _insert(bare_db, "claude-sonnet-4-6", input_usd=3.0, output_usd=15.0,
            cache_read=0.3, cache_creation=3.75)
    _insert(bare_db, "__default__", input_usd=15.0, output_usd=75.0,
            cache_read=1.5, cache_creation=18.75)

    result = _lookup_price(bare_db, "claude-sonnet-4-6-20260315", _NOW)

    assert result is not None, "_lookup_price returned None; expected base-model rates"
    assert result["input"] == pytest.approx(3.0, rel=1e-9), (
        f"Expected base sonnet rate 3.0, got {result['input']} "
        "(likely fell through to __default__ at 15.0)"
    )


# ---------------------------------------------------------------------------
# Test 2 — AC-2: exact dated match wins over base-strip fallback
# ---------------------------------------------------------------------------

def test_exact_dated_match_wins_over_base_strip(bare_db):
    """When an exact dated row exists, it wins over the stripped base row.

    Seed:    claude-haiku-4-5          (input=0.8)
             claude-haiku-4-5-20251001 (input=0.7, sentinel)
    Query:   claude-haiku-4-5-20251001
    Expect:  input=0.7  (exact dated row), NOT 0.8 (base)
    """
    _insert(bare_db, "claude-haiku-4-5", input_usd=0.8)
    _insert(bare_db, "claude-haiku-4-5-20251001", input_usd=0.7)

    result = _lookup_price(bare_db, "claude-haiku-4-5-20251001", _NOW)

    assert result is not None, "_lookup_price returned None for a seeded exact dated row"
    assert result["input"] == pytest.approx(0.7, rel=1e-9), (
        f"Expected exact dated rate 0.7, got {result['input']} "
        "(should prefer exact match over base-strip)"
    )


# ---------------------------------------------------------------------------
# Test 3 — AC-3: non-dated unknown model falls back to __default__
# ---------------------------------------------------------------------------

def test_non_dated_unknown_model_falls_back_to_default(bare_db):
    """An unknown model with no date suffix falls back to __default__.

    Seed:    __default__  (input=15.0)
    Query:   claude-future-99
    Expect:  input=15.0  (__default__ rates)
    """
    _insert(bare_db, "__default__", input_usd=15.0, output_usd=75.0,
            cache_read=1.5, cache_creation=18.75)

    result = _lookup_price(bare_db, "claude-future-99", _NOW)

    assert result is not None, "_lookup_price returned None; expected __default__ fallback"
    assert result["input"] == pytest.approx(15.0, rel=1e-9), (
        f"Expected __default__ rate 15.0, got {result['input']}"
    )


# ---------------------------------------------------------------------------
# Test 4 — AC-4: dated ID with unseeded base falls back to __default__
# ---------------------------------------------------------------------------

def test_dated_id_with_unseeded_base_falls_back_to_default(bare_db):
    """A dated ID whose base model is not seeded falls back to __default__.

    Seed:    __default__  (input=15.0)
    Query:   unknown-model-20260101
    Expect:  input=15.0  (__default__ rates)
    """
    _insert(bare_db, "__default__", input_usd=15.0, output_usd=75.0,
            cache_read=1.5, cache_creation=18.75)

    result = _lookup_price(bare_db, "unknown-model-20260101", _NOW)

    assert result is not None, "_lookup_price returned None; expected __default__ fallback"
    assert result["input"] == pytest.approx(15.0, rel=1e-9), (
        f"Expected __default__ rate 15.0, got {result['input']}"
    )
