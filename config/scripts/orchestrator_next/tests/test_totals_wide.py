"""
T-3 (RED) / T-4 (GREEN): _totals() wide projection test.
T-7 (RED) / T-8 (GREEN): _load_pricing_for_model(db, model) direct tests.

Tests that _totals() returns:
  - cache_creation_input_tokens (SUM)
  - cache_read_input_tokens (SUM)
  - turns (SUM)
  - model (dominant model by input_tokens)
  - pricing (sub-dict with input, output, cache_read, cache_creation rates)
  - gross_usd (computed at full rates, no cache discount)

gross_usd formula (from task instructions):
  gross = (input_tokens + cache_creation_input_tokens + cache_read_input_tokens)
          * input_rate / 1_000_000
          + output_tokens * output_rate / 1_000_000

This is the economic "gross" — what you'd pay at sticker price without
cache savings; it differs from net_usd (cost_usd SUM which is cache-discounted).

T-7 additionally tests _load_pricing_for_model(db, model) directly:
  - Seeded DB + known model → expected rates from pricing table
  - Seeded DB + unknown model → __default__ rates (opus-tier fallback)
  - Read-only DB with no pricing table → built-in conservative fallback, no raise (AC-6)
"""
from __future__ import annotations

import os
import sys

import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Sonnet-4-5 pricing rates from config/pricing.yaml
_SONNET_4_5_RATES = {
    "input": 3.00,
    "output": 15.00,
    "cache_read": 0.30,
    "cache_creation": 3.75,
}


def _seed_step_events(db, repo_root: str, change_id: str) -> None:
    """
    Seed two step_events rows for the given feature with claude-sonnet-4-5,
    one carrying cache tokens and one carrying turns.
    """
    from orchestrator_next.upsert import ensure_schema, upsert_step_event
    from orchestrator_next.parser import StepHistoryEntry

    ensure_schema(db)

    # Row 1: has cache tokens
    entry1 = StepHistoryEntry(
        step_id="step-explore",
        phase="specify",
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-04-20T00:00:00Z",
        ended_at="2026-04-20T00:01:00Z",
        usage={
            "model": "claude-sonnet-4-5",
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 300,
            "turns": 5,
            "cost_usd": 0.01,
        },
        escalation=None,
        raw={},
    )
    # Row 2: more input tokens (to confirm sonnet-4-5 is dominant), more turns
    entry2 = StepHistoryEntry(
        step_id="step-implement",
        phase="implement",
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-04-20T00:02:00Z",
        ended_at="2026-04-20T00:03:00Z",
        usage={
            "model": "claude-sonnet-4-5",
            "input_tokens": 2000,
            "output_tokens": 400,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 100,
            "turns": 37,
            "cost_usd": 0.02,
        },
        escalation=None,
        raw={},
    )

    ctx = {"repo_root": repo_root, "change_id": change_id}
    upsert_step_event(db, entry1, ctx)
    upsert_step_event(db, entry2, ctx)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_totals_includes_cache_creation_tokens():
    """_totals() must return cache_creation_input_tokens as a key."""
    from orchestrator_next.cost_report import _totals

    db = duckdb.connect(":memory:")
    try:
        repo_root, change_id = "/test/repo", "test-feature"
        _seed_step_events(db, repo_root, change_id)

        totals = _totals(db, repo_root, change_id)
        assert "cache_creation_input_tokens" in totals, (
            f"'cache_creation_input_tokens' missing from _totals(). Keys: {list(totals)}"
        )
        assert totals["cache_creation_input_tokens"] == 500, (
            f"Expected 500, got {totals['cache_creation_input_tokens']}"
        )
    finally:
        db.close()


def test_totals_includes_cache_read_tokens():
    """_totals() must return cache_read_input_tokens as a key."""
    from orchestrator_next.cost_report import _totals

    db = duckdb.connect(":memory:")
    try:
        repo_root, change_id = "/test/repo", "test-feature"
        _seed_step_events(db, repo_root, change_id)

        totals = _totals(db, repo_root, change_id)
        assert "cache_read_input_tokens" in totals, (
            f"'cache_read_input_tokens' missing from _totals(). Keys: {list(totals)}"
        )
        assert totals["cache_read_input_tokens"] == 400, (
            f"Expected 400 (300+100), got {totals['cache_read_input_tokens']}"
        )
    finally:
        db.close()


def test_totals_includes_turns():
    """_totals() must return turns as a key with the summed value."""
    from orchestrator_next.cost_report import _totals

    db = duckdb.connect(":memory:")
    try:
        repo_root, change_id = "/test/repo", "test-feature"
        _seed_step_events(db, repo_root, change_id)

        totals = _totals(db, repo_root, change_id)
        assert "turns" in totals, (
            f"'turns' missing from _totals(). Keys: {list(totals)}"
        )
        assert totals["turns"] == 42, (
            f"Expected turns=42 (5+37), got {totals['turns']}"
        )
    finally:
        db.close()


def test_totals_includes_dominant_model():
    """_totals() must return model (dominant by input_tokens)."""
    from orchestrator_next.cost_report import _totals

    db = duckdb.connect(":memory:")
    try:
        repo_root, change_id = "/test/repo", "test-feature"
        _seed_step_events(db, repo_root, change_id)

        totals = _totals(db, repo_root, change_id)
        assert "model" in totals, (
            f"'model' missing from _totals(). Keys: {list(totals)}"
        )
        assert totals["model"] == "claude-sonnet-4-5", (
            f"Expected 'claude-sonnet-4-5', got {totals['model']!r}"
        )
    finally:
        db.close()


def test_totals_includes_pricing_subdict():
    """_totals() must return pricing sub-dict with four rate keys."""
    from orchestrator_next.cost_report import _totals

    db = duckdb.connect(":memory:")
    try:
        repo_root, change_id = "/test/repo", "test-feature"
        _seed_step_events(db, repo_root, change_id)

        totals = _totals(db, repo_root, change_id)
        assert "pricing" in totals, (
            f"'pricing' missing from _totals(). Keys: {list(totals)}"
        )
        pricing = totals["pricing"]
        assert isinstance(pricing, dict), f"Expected dict, got {type(pricing)}"
        for rate_key in ("input", "output", "cache_read", "cache_creation"):
            assert rate_key in pricing, (
                f"pricing['{rate_key}'] missing. pricing keys: {list(pricing)}"
            )
        # Verify rates match pricing.yaml for claude-sonnet-4-5
        assert pricing["input"] == 3.00, f"Expected input=3.00, got {pricing['input']}"
        assert pricing["output"] == 15.00, f"Expected output=15.00, got {pricing['output']}"
        assert pricing["cache_read"] == 0.30, f"Expected cache_read=0.30, got {pricing['cache_read']}"
        assert pricing["cache_creation"] == 3.75, f"Expected cache_creation=3.75, got {pricing['cache_creation']}"
    finally:
        db.close()


def test_totals_includes_gross_usd():
    """
    _totals() must return gross_usd computed at full rates (no cache discount).

    Formula (from task instructions):
      gross = (input_tokens + cache_creation_input_tokens + cache_read_input_tokens)
              * input_rate / 1_000_000
              + output_tokens * output_rate / 1_000_000

    Seeded values (both rows):
      input_tokens=3000, cache_creation=500, cache_read=400, output_tokens=600
      rates: input=3.00, output=15.00
    Expected:
      gross = (3000 + 500 + 400) * 3.00 / 1_000_000 + 600 * 15.00 / 1_000_000
            = 3900 * 3.00 / 1_000_000 + 600 * 15.00 / 1_000_000
            = 0.0117 + 0.009
            = 0.0207
    """
    from orchestrator_next.cost_report import _totals

    db = duckdb.connect(":memory:")
    try:
        repo_root, change_id = "/test/repo", "test-feature"
        _seed_step_events(db, repo_root, change_id)

        totals = _totals(db, repo_root, change_id)
        assert "gross_usd" in totals, (
            f"'gross_usd' missing from _totals(). Keys: {list(totals)}"
        )

        # Seeded: input=1000+2000=3000, output=200+400=600
        # cache_creation=500+0=500, cache_read=300+100=400
        input_tok = 3000
        cache_create = 500
        cache_read = 400
        output_tok = 600
        rates = _SONNET_4_5_RATES
        expected_gross = (
            (input_tok + cache_create + cache_read) * rates["input"] / 1_000_000
            + output_tok * rates["output"] / 1_000_000
        )
        assert abs(totals["gross_usd"] - expected_gross) < 1e-9, (
            f"Expected gross_usd={expected_gross}, got {totals['gross_usd']}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# T-7 (RED): direct tests for _load_pricing_for_model(db, model)
# These tests call the helper directly, verifying the (db, model) signature.
# They FAIL on current code because the signature is still (model) not (db, model).
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_db():
    """In-memory DuckDB with full schema including seeded pricing rows (via _run_migrations)."""
    from orchestrator_next.upsert import ensure_schema
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


def test_load_pricing_known_model(seeded_db):
    """Seeded DB + known model → returns expected rates from pricing table.

    claude-sonnet-4-6 rates from 0001_seed_pricing.sql:
      input=3.00, output=15.00, cache_read=0.30, cache_creation=3.75
    """
    from orchestrator_next.cost_report import _load_pricing_for_model

    result = _load_pricing_for_model(seeded_db, "claude-sonnet-4-6")

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["input"] == 3.00, f"Expected input=3.00, got {result['input']}"
    assert result["output"] == 15.00, f"Expected output=15.00, got {result['output']}"
    assert result["cache_read"] == 0.30, f"Expected cache_read=0.30, got {result['cache_read']}"
    assert result["cache_creation"] == 3.75, f"Expected cache_creation=3.75, got {result['cache_creation']}"


def test_load_pricing_unknown_model_falls_back_to_default(seeded_db):
    """Seeded DB + unknown model → returns __default__ rates (opus-tier sentinel).

    __default__ rates from 0001_seed_pricing.sql:
      input=15.00, output=75.00, cache_read=1.50, cache_creation=18.75
    """
    from orchestrator_next.cost_report import _load_pricing_for_model

    result = _load_pricing_for_model(seeded_db, "model-that-does-not-exist")

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["input"] == 15.00, f"Expected input=15.00 (__default__), got {result['input']}"
    assert result["output"] == 75.00, f"Expected output=75.00 (__default__), got {result['output']}"
    assert result["cache_read"] == 1.50, f"Expected cache_read=1.50 (__default__), got {result['cache_read']}"
    assert result["cache_creation"] == 18.75, f"Expected cache_creation=18.75 (__default__), got {result['cache_creation']}"


def test_load_pricing_no_pricing_table_read_only_returns_fallback(tmp_path):
    """Read-only DB with NO pricing table → returns built-in conservative fallback, no raise.

    This is AC-6: cost_report must degrade gracefully on DBs predating the
    pricing migration (e.g. a DB opened read_only=True that was never migrated).

    Built-in fallback dict: {input:15.0, output:75.0, cache_read:1.5, cache_creation:18.75}
    """
    from orchestrator_next.cost_report import _load_pricing_for_model

    # Create a minimal DB file WITHOUT calling ensure_schema (so no pricing table).
    db_path = tmp_path / "no_pricing.duckdb"
    setup_db = duckdb.connect(str(db_path))
    # Only create step_events stub — pricing table intentionally omitted.
    setup_db.execute("CREATE TABLE step_events (id INTEGER)")
    setup_db.close()

    # Open read_only — any attempt to query a non-existent 'pricing' table raises duckdb.Error.
    ro_db = duckdb.connect(str(db_path), read_only=True)
    try:
        # Must NOT raise, must return the built-in conservative fallback.
        result = _load_pricing_for_model(ro_db, "claude-sonnet-4-6")
    finally:
        ro_db.close()

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["input"] == 15.0, f"Expected input=15.0 (fallback), got {result['input']}"
    assert result["output"] == 75.0, f"Expected output=75.0 (fallback), got {result['output']}"
    assert result["cache_read"] == 1.5, f"Expected cache_read=1.5 (fallback), got {result['cache_read']}"
    assert result["cache_creation"] == 18.75, f"Expected cache_creation=18.75 (fallback), got {result['cache_creation']}"
