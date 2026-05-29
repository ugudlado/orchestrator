"""T-5 RED: tests for _lookup_price and DuckDB-backed cost compute.

Six scenarios:
  1. Exact model hit → returns correct rate tuple for claude-sonnet-4-6
  2. Unknown model → __default__ fallback rates
  3. Two effective_from rows for same model → latest <= now wins
  4. Both model and __default__ absent → returns None, emits stderr warning
  5. db=None → returns None, emits stderr warning (offline/test path)
  6. Micro-benchmark: 1000 calls on warmed-up connection < 50 ms (NFR-1)
"""
from __future__ import annotations

import datetime
import sys
import time
from io import StringIO

import duckdb
import pytest

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import orchestrator_next.record as _record_mod
from orchestrator_next.record import _lookup_price  # noqa: E402
from orchestrator_next.upsert import ensure_schema   # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_pricing_cache():
    """Clear the module-level _pricing_cache before each test.

    _pricing_cache is keyed by id(db). CPython can reuse the same memory
    address for a new connection after a previous one is GC'd, which would
    return stale rows from a prior test's connection. Clearing between tests
    prevents this latent flake.
    """
    _record_mod._pricing_cache.clear()
    yield
    _record_mod._pricing_cache.clear()


@pytest.fixture()
def in_memory_db():
    """In-memory DuckDB with full schema (runs migrations, seeds pricing)."""
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Reference data (from migration 0001_seed_pricing.sql)
# ---------------------------------------------------------------------------

_EPOCH = datetime.datetime(2025, 1, 1, 0, 0, 0)  # effective_from for seeded rows
_NOW = datetime.datetime(2026, 4, 20, 12, 0, 0)   # well after _EPOCH

_SONNET_RATES = {
    "input": 3.00,
    "output": 15.00,
    "cache_read": 0.30,
    "cache_creation": 3.75,
}

_DEFAULT_RATES = {
    "input": 15.00,
    "output": 75.00,
    "cache_read": 1.50,
    "cache_creation": 18.75,
}


# ---------------------------------------------------------------------------
# Scenario 1: Exact model hit
# ---------------------------------------------------------------------------

class TestExactModelHit:

    def test_returns_dict_for_known_model(self, in_memory_db):
        """_lookup_price with a seeded model returns a non-None dict."""
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)
        assert result is not None, "_lookup_price returned None for a seeded model"

    def test_returns_correct_input_rate(self, in_memory_db):
        """input rate matches pricing migration for claude-sonnet-4-6."""
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)
        assert result["input"] == pytest.approx(3.00, rel=1e-9)

    def test_returns_correct_output_rate(self, in_memory_db):
        """output rate matches pricing migration for claude-sonnet-4-6."""
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)
        assert result["output"] == pytest.approx(15.00, rel=1e-9)

    def test_returns_correct_cache_read_rate(self, in_memory_db):
        """cache_read rate matches pricing migration for claude-sonnet-4-6."""
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)
        assert result["cache_read"] == pytest.approx(0.30, rel=1e-9)

    def test_returns_correct_cache_creation_rate(self, in_memory_db):
        """cache_creation rate matches pricing migration for claude-sonnet-4-6."""
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)
        assert result["cache_creation"] == pytest.approx(3.75, rel=1e-9)


# ---------------------------------------------------------------------------
# Scenario 2: Unknown model → __default__ fallback
# ---------------------------------------------------------------------------

class TestDefaultFallback:

    def test_unknown_model_returns_default_rates(self, in_memory_db):
        """An unknown model falls back to __default__ row."""
        result = _lookup_price(in_memory_db, "does-not-exist", _NOW)
        assert result is not None, "_lookup_price returned None; expected __default__ fallback"
        assert result["input"] == pytest.approx(15.00, rel=1e-9)
        assert result["output"] == pytest.approx(75.00, rel=1e-9)
        assert result["cache_read"] == pytest.approx(1.50, rel=1e-9)
        assert result["cache_creation"] == pytest.approx(18.75, rel=1e-9)


# ---------------------------------------------------------------------------
# Scenario 3: Two effective_from rows → latest <= now wins
# ---------------------------------------------------------------------------

class TestEffectiveFromOrdering:

    def test_returns_newer_rates_after_effective_date(self, in_memory_db):
        """When a second (newer) row exists, query at that time returns new rates."""
        new_effective = datetime.datetime(2026, 1, 1, 0, 0, 0)
        in_memory_db.execute(
            "INSERT OR REPLACE INTO pricing "
            "(model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, "
            "is_local, effective_from) VALUES (?, ?, ?, ?, ?, FALSE, ?)",
            ["claude-sonnet-4-6", 5.00, 25.00, 0.50, 6.25, new_effective],
        )

        query_time = datetime.datetime(2026, 2, 1, 0, 0, 0)  # after new_effective
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", query_time)
        assert result is not None
        assert result["input"] == pytest.approx(5.00, rel=1e-9), (
            f"Expected new rate 5.00, got {result['input']}"
        )

    def test_returns_older_rates_before_effective_date(self, in_memory_db):
        """Before the newer row's effective_from, original rates are returned."""
        new_effective = datetime.datetime(2026, 1, 1, 0, 0, 0)
        in_memory_db.execute(
            "INSERT OR REPLACE INTO pricing "
            "(model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, "
            "is_local, effective_from) VALUES (?, ?, ?, ?, ?, FALSE, ?)",
            ["claude-sonnet-4-6", 5.00, 25.00, 0.50, 6.25, new_effective],
        )

        query_time = datetime.datetime(2025, 6, 1, 0, 0, 0)  # before new_effective
        result = _lookup_price(in_memory_db, "claude-sonnet-4-6", query_time)
        assert result is not None
        assert result["input"] == pytest.approx(3.00, rel=1e-9), (
            f"Expected original rate 3.00, got {result['input']}"
        )


# ---------------------------------------------------------------------------
# Scenario 4: Both model and __default__ absent → None + stderr warning
# ---------------------------------------------------------------------------

class TestBothAbsent:

    def test_returns_none_when_no_rows(self, in_memory_db):
        """Returns None when both model and __default__ are absent."""
        in_memory_db.execute("DELETE FROM pricing")

        result = _lookup_price(in_memory_db, "x", _NOW)
        assert result is None, f"Expected None when pricing is empty, got {result}"

    def test_emits_stderr_warning_when_no_rows(self, in_memory_db, capsys):
        """Emits a stderr warning when both model and __default__ are absent."""
        in_memory_db.execute("DELETE FROM pricing")

        _lookup_price(in_memory_db, "x", _NOW)
        captured = capsys.readouterr()
        assert captured.err, "Expected a stderr warning when pricing rows are absent"


# ---------------------------------------------------------------------------
# Scenario 5: db=None → None + stderr warning (offline/test path)
# ---------------------------------------------------------------------------

class TestDbNone:

    def test_returns_none_when_db_is_none(self, capsys):
        """_lookup_price(None, ...) returns None (offline path)."""
        result = _lookup_price(None, "claude-sonnet-4-6", _NOW)
        assert result is None, f"Expected None for db=None, got {result}"

    def test_emits_stderr_warning_when_db_is_none(self, capsys):
        """_lookup_price(None, ...) emits a stderr warning."""
        _lookup_price(None, "claude-sonnet-4-6", _NOW)
        captured = capsys.readouterr()
        assert captured.err, "Expected a stderr warning for db=None"


# ---------------------------------------------------------------------------
# Scenario 6: Micro-benchmark (NFR-1) — 1000 calls < 50 ms on warmed connection
# ---------------------------------------------------------------------------

class TestMicroBenchmark:

    def test_1000_calls_under_50ms(self, in_memory_db):
        """1000 _lookup_price calls on a warmed connection complete in < 50 ms."""
        # Warm-up: 10 primer calls outside the timed window
        for _ in range(10):
            _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)

        start = time.perf_counter()
        for _ in range(1000):
            _lookup_price(in_memory_db, "claude-sonnet-4-6", _NOW)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50, (
            f"1000 _lookup_price calls took {elapsed_ms:.1f} ms (budget: 50 ms). "
            "Consider adding a SELECT-plan cache or reducing query overhead."
        )
