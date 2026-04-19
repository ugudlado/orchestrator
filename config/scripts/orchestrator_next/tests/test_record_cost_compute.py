"""T-4: Regression tests — record() populates usage.model and usage.cost_usd (RED).

Five cases proving ISSUE-17:
  1. developer agent (native_sonnet) → cost_usd computed, model set to claude-sonnet-4-6  [RED]
  2. architect agent (native_opus)   → cost_usd computed using opus rates               [RED]
  3. cache_read_input_tokens present → included in cost at cache_read rate               [RED]
  4. payload already has cost_usd    → existing value preserved, not clobbered            [may pass on RED]
  5. inline agent (unresolvable)     → cost_usd stays unset, no exception                [may pass on RED]

Cases 1–3 FAIL before T-5 adds the computation logic.
Cases 4–5 may accidentally pass since record.py already passes usage through untouched.
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import record  # noqa: E402


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
    """Write a minimal state.yaml and return its path."""
    state = {
        "change_id": "cost-compute-test",
        "phase": "implement",
        "workflow_plan": {
            "implement": {
                "active": ["execute-next-task"],
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
# Test cases
# ---------------------------------------------------------------------------

class TestRecordCostCompute:

    @pytest.fixture(autouse=True)
    def setup_contracts_and_clear_cache(self, tmp_path, monkeypatch):
        """Isolate contracts and clear lru_cache so each test starts fresh.

        Sets ORCHESTRATOR_HOME to the worktree root so _load_routes() and
        _load_pricing() read the worktree's config files (which have the
        backends: block added in T-5), not whatever ORCHESTRATOR_HOME points
        to in the shell environment.
        """
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "execute-next-task.yaml").write_text(_STUB_CONTRACT)
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
        )
        # Point ORCHESTRATOR_HOME at the worktree so routes.yaml / pricing.yaml
        # are read from the right place regardless of the shell environment.
        _worktree_root = str(
            Path(_HERE).parent.parent.parent.parent  # tests/ → orchestrator_next/ → scripts/ → config/ → worktree
        )
        monkeypatch.setenv("ORCHESTRATOR_HOME", _worktree_root)
        # Clear lru_cache on cost loaders so tests see fresh data.
        # The cache functions will exist after T-5 is implemented; guard with
        # hasattr so this fixture doesn't break RED runs.
        import orchestrator_next.record as record_mod
        for fn_name in ("_load_routes", "_load_pricing"):
            fn = getattr(record_mod, fn_name, None)
            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()

    def test_computes_cost_for_native_sonnet_agent(self, tmp_path):
        """ISSUE-17 RED: developer → native_sonnet → claude-sonnet-4-6.

        After record(), usage.cost_usd should be:
          22000 * 3.0/1e6 + 5000 * 15.0/1e6 = 0.141000
        and usage.model should be 'claude-sonnet-4-6'.
        Before T-5 this FAILS because no cost computation exists.
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="developer",
            usage={"input_tokens": 22000, "output_tokens": 5000, "duration_ms": 45000},
        )
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        expected_cost = 22000 * 3.0 / 1_000_000 + 5000 * 15.0 / 1_000_000

        assert usage.get("model") == "claude-sonnet-4-6", (
            f"Expected model='claude-sonnet-4-6', got {usage.get('model')!r}"
        )
        assert usage.get("cost_usd") == pytest.approx(expected_cost, rel=1e-6), (
            f"Expected cost_usd≈{expected_cost}, got {usage.get('cost_usd')!r}"
        )

    def test_computes_cost_for_native_opus_agent(self, tmp_path):
        """ISSUE-17 RED: architect → native_opus → claude-opus-4-7.

        After record(), usage.cost_usd should use opus rates:
          10000 * 15.0/1e6 + 2000 * 75.0/1e6 = 0.300000
        and usage.model should be 'claude-opus-4-7'.
        Before T-5 this FAILS.
        """
        state_path = _write_state(tmp_path)
        payload = _base_payload(
            agent="architect",
            usage={"input_tokens": 10000, "output_tokens": 2000, "duration_ms": 30000},
        )
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        expected_cost = 10000 * 15.0 / 1_000_000 + 2000 * 75.0 / 1_000_000

        assert usage.get("model") == "claude-opus-4-7", (
            f"Expected model='claude-opus-4-7', got {usage.get('model')!r}"
        )
        assert usage.get("cost_usd") == pytest.approx(expected_cost, rel=1e-6), (
            f"Expected cost_usd≈{expected_cost}, got {usage.get('cost_usd')!r}"
        )

    def test_includes_cache_read_tokens_when_present(self, tmp_path):
        """ISSUE-17 RED: cache_read_input_tokens should be included in cost.

        With developer (claude-sonnet-4-6):
          22000 * 3.0/1e6 + 5000 * 15.0/1e6 + 10000 * 0.30/1e6 = 0.144000
        Before T-5 this FAILS.
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
        result, exit_code = record(state_path, payload)
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

    def test_preserves_existing_cost_usd(self, tmp_path):
        """Pre-computed cost_usd in payload must not be overwritten.

        When usage already contains cost_usd=0.42, record() should store
        exactly 0.42 — the guard `not usage.get('cost_usd')` prevents re-computation.
        This case may pass on RED because record.py already passes usage through.
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
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"record() failed: {result}"

        usage = _get_recorded_usage(state_path)
        assert usage.get("cost_usd") == pytest.approx(0.42, rel=1e-9), (
            f"Expected cost_usd=0.42 preserved, got {usage.get('cost_usd')!r}"
        )
        assert usage.get("model") == "claude-sonnet-4-6", (
            f"Expected model unchanged, got {usage.get('model')!r}"
        )

    def test_skips_when_agent_unresolvable(self, tmp_path):
        """Inline agent (not in routes.yaml) — cost_usd should stay unset, no exception.

        Using agent=inline which is exempt from Check B (usage not required).
        cost_usd absent from the written entry is acceptable — fail-open behaviour.
        This case may pass on RED because record.py doesn't compute cost today.
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
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"record() unexpectedly failed: {result}"

        usage = _get_recorded_usage(state_path)
        assert usage.get("cost_usd") is None, (
            f"Expected cost_usd=None for inline agent, got {usage.get('cost_usd')!r}"
        )
