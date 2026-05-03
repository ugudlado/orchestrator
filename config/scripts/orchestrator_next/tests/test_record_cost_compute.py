"""T-5/T-6: record() populates usage.model and usage.cost_usd via DuckDB.

Seven cases:
  1. developer agent (native_sonnet) → cost_usd computed, model set to claude-sonnet-4-6
  2. architect agent (native_opus)   → cost_usd computed using opus rates
  3. cache_read_input_tokens present → included in cost at cache_read rate
  4. payload already has cost_usd    → existing value preserved, not clobbered
  5. inline agent, no tokens         → cost_usd stays unset, no exception
  6. inline agent + tokens           → cost_usd from DuckDB __default__ row, model __default__
  7. db=None                         → no exception, cost_usd unset (pricing skipped)
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import orchestrator_next.record as _record_mod
from orchestrator_next.record import record  # noqa: E402
from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Stub contract with NO repeat_until so we don't trip the repeat logic.
# Must include `outputs: [task_execution_result]` to satisfy contract validation.
_STUB_CONTRACT = textwrap.dedent("""\
    id: execute-next-task
    agent: developer
    instruction: Execute the next task.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
""")


def _write_state(tmp_path) -> str:
    """Write a minimal state.yaml and return its path.

    Note: 'trailing-step' is intentionally added after 'execute-next-task'
    so that execute-next-task is NOT the last step (not a boundary step).
    This prevents _detect_boundary() from triggering FEATURE boundary logic,
    which would require a real JSONL session file to exist.
    """
    state = {
        "change_id": "cost-compute-test",
        "phase": "implement",
        "workflow_plan": {
            "implement": {
                "active": ["execute-next-task", "trailing-step"],
                "filtered": [],
            }
        },
        "step_history": [],
        "worktree_path": str(tmp_path),
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _base_payload(agent: str, usage: dict) -> dict:
    """Build a completed step payload for execute-next-task."""
    return {
        "step_id": "execute-next-task",
        "phase": "implement",
        "status": "completed",
        "agent": agent,
        "outputs": {"task_execution_result": {"task_id": "T-1"}},
        "usage": usage,
    }


def _get_recorded_usage(state_path: str) -> dict:
    """Read the last step_history entry's usage from state.yaml."""
    with open(state_path) as f:
        state = yaml.safe_load(f)
    history = state.get("step_history") or []
    assert history, "step_history is empty after record()"
    return history[-1].get("usage") or {}


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
    """In-memory DuckDB with full schema including seeded pricing rows."""
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestRecordCostCompute:

    @pytest.fixture(autouse=True)
    def setup_contracts_and_clear_cache(self, tmp_path, monkeypatch):
        """Isolate contracts and clear lru_cache so each test starts fresh.

        Sets ORCHESTRATOR_HOME to the worktree root so _load_routes() reads
        the worktree's config files (routes.yaml still lives in YAML).
        Pricing now comes from in_memory_db; _load_pricing is gone.
        """
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "execute-next-task.yaml").write_text(_STUB_CONTRACT)
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
        )
        # Point ORCHESTRATOR_HOME at the worktree so routes.yaml is read from
        # the right place regardless of the shell environment.
        _worktree_root = str(
            Path(_HERE).parent.parent.parent.parent  # tests/ → orchestrator_next/ → scripts/ → config/ → worktree
        )
        monkeypatch.setenv("ORCHESTRATOR_HOME", _worktree_root)
        # Clear lru_cache on routes loader so tests see fresh data.
        import orchestrator_next.record as record_mod
        fn = getattr(record_mod, "_load_routes", None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()

    def test_computes_cost_for_native_sonnet_agent(self, tmp_path, in_memory_db):
        """developer → native_sonnet → claude-sonnet-4-6.

        After record(), usage.cost_usd should be:
          22000 * 3.0/1e6 + 5000 * 15.0/1e6 = 0.141000
        and usage.model should be 'claude-sonnet-4-6'.
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="developer",
            usage={"input_tokens": 22000, "output_tokens": 5000, "duration_ms": 45000},
        )
        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        expected_cost = 22000 * 3.0 / 1_000_000 + 5000 * 15.0 / 1_000_000

        assert usage.get("model") == "claude-sonnet-4-6", (
            f"Expected model='claude-sonnet-4-6', got {usage.get('model')!r}"
        )
        assert usage.get("cost_usd") == pytest.approx(expected_cost, rel=1e-6), (
            f"Expected cost_usd≈{expected_cost}, got {usage.get('cost_usd')!r}"
        )

    def test_computes_cost_for_native_opus_agent(self, tmp_path, in_memory_db):
        """architect → native_opus → claude-opus-4-7.

        After record(), usage.cost_usd should use opus rates:
          10000 * 15.0/1e6 + 2000 * 75.0/1e6 = 0.300000
        and usage.model should be 'claude-opus-4-7'.
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="architect",
            usage={"input_tokens": 10000, "output_tokens": 2000, "duration_ms": 30000},
        )
        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        expected_cost = 10000 * 15.0 / 1_000_000 + 2000 * 75.0 / 1_000_000

        assert usage.get("model") == "claude-opus-4-7", (
            f"Expected model='claude-opus-4-7', got {usage.get('model')!r}"
        )
        assert usage.get("cost_usd") == pytest.approx(expected_cost, rel=1e-6), (
            f"Expected cost_usd≈{expected_cost}, got {usage.get('cost_usd')!r}"
        )

    def test_includes_cache_read_tokens_when_present(self, tmp_path, in_memory_db):
        """cache_read_input_tokens should be included in cost.

        With developer (claude-sonnet-4-6):
          22000 * 3.0/1e6 + 5000 * 15.0/1e6 + 10000 * 0.30/1e6 = 0.144000
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="developer",
            usage={
                "input_tokens": 22000,
                "output_tokens": 5000,
                "cache_read_input_tokens": 10000,
                "duration_ms": 45000,
            },
        )
        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        expected_cost = (
            22000 * 3.0 / 1_000_000
            + 5000 * 15.0 / 1_000_000
            + 10000 * 0.30 / 1_000_000
        )

        assert usage.get("cost_usd") == pytest.approx(expected_cost, rel=1e-6), (
            f"Expected cost_usd≈{expected_cost} (includes cache_read), got {usage.get('cost_usd')!r}"
        )

    def test_preserves_existing_cost_usd(self, tmp_path, in_memory_db):
        """Pre-computed cost_usd in payload must not be overwritten.

        When usage already contains cost_usd=0.42, record() should store
        exactly 0.42 — the guard `not usage.get('cost_usd')` prevents re-computation.
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="developer",
            usage={
                "input_tokens": 22000,
                "output_tokens": 5000,
                "model": "claude-sonnet-4-6",
                "cost_usd": 0.42,
            },
        )
        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        assert usage.get("cost_usd") == pytest.approx(0.42, rel=1e-9), (
            f"Expected cost_usd=0.42 preserved, got {usage.get('cost_usd')!r}"
        )
        assert usage.get("model") == "claude-sonnet-4-6", (
            f"Expected model unchanged, got {usage.get('model')!r}"
        )

    def test_skips_when_agent_unresolvable(self, tmp_path, in_memory_db):
        """Inline agent with no billable tokens — cost_usd stays unset, no exception.

        Using agent=inline which is exempt from Check B (usage not required).
        cost_usd absent from the written entry is acceptable — fail-open behaviour.
        """
        state_path = _write_state(tmp_path)
        # inline agent is exempt from Check B (agent_step_missing_usage).
        payload = {
            "step_id": "execute-next-task",
            "phase": "implement",
            "status": "completed",
            "agent": "inline",
            "outputs": {"task_execution_result": {"task_id": "T-1"}},
            "usage": {},
        }
        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() unexpectedly failed: {result}"

        usage = _get_recorded_usage(state_path)
        assert usage.get("cost_usd") is None, (
            f"Expected cost_usd=None for inline agent, got {usage.get('cost_usd')!r}"
        )

    def test_computes_cost_for_inline_when_tokens_use_default_pricing(
        self, tmp_path, in_memory_db
    ):
        """inline + input/output tokens → priced via __default__ row (no routes entry)."""
        state_path = _write_state(tmp_path)
        payload = {
            "step_id": "execute-next-task",
            "phase": "implement",
            "status": "completed",
            "agent": "inline",
            "outputs": {"task_execution_result": {"task_id": "T-1"}},
            "usage": {"input_tokens": 2000, "output_tokens": 1000, "duration_ms": 1},
        }
        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        # __default__ from 0001_seed_pricing.sql: input 15.00 / MTok, output 75.00 / MTok
        expected_cost = 2000 * 15.0 / 1_000_000 + 1000 * 75.0 / 1_000_000
        assert usage.get("model") == "__default__", (
            f"Expected model='__default__', got {usage.get('model')!r}"
        )
        assert usage.get("cost_usd") == pytest.approx(expected_cost, rel=1e-6), (
            f"Expected cost_usd≈{expected_cost}, got {usage.get('cost_usd')!r}"
        )

    def test_db_none_no_exception_and_cost_usd_unset(self, tmp_path):
        """record(state_yaml_path, payload, db=None) — offline/test path.

        No exception raised; usage.cost_usd remains unset (pricing not invoked).
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="developer",
            usage={"input_tokens": 22000, "output_tokens": 5000},
        )
        # db=None is the offline path — no DB connection available
        result, exit_code = record(state_path, payload, db=None)
        assert exit_code == 0, f"record() should not fail with db=None: {result}"

        usage = _get_recorded_usage(state_path)
        assert usage.get("cost_usd") is None, (
            f"Expected cost_usd=None with db=None, got {usage.get('cost_usd')!r}"
        )
