"""`node_metrics` / `metrics_totals` (protocol.py) fold step_history into rows.

These are the per-node numbers `orchestrator status --json` reports so the Mod
pane can draw a metrics table without folding `events --json` itself. Pure
helpers over a raw history list, so they are tested directly rather than
through a run.
"""
from __future__ import annotations

from orchestrator_next.protocol import metrics_totals, node_metrics


def _entry(step_id: str, **usage: object) -> dict:
    outputs = usage.pop("outputs", {})
    return {
        "step_id": step_id,
        "started_at": usage.pop("started_at", "2026-01-01T00:00:00+00:00"),
        "ended_at": usage.pop("ended_at", "2026-01-01T00:00:12+00:00"),
        "outputs": outputs,
        "usage": dict(usage),
    }


def test_single_attempt_projects_every_column() -> None:
    rows = node_metrics([
        _entry(
            "discovery",
            model="claude-sonnet-5",
            input_tokens=1200,
            output_tokens=340,
            cache_read_input_tokens=12500,
            cache_creation_input_tokens=800,
            cost_usd=0.1731,
            duration_ms=12000,
        )
    ])
    assert rows["discovery"] == {
        "attempts": 1,
        "model": "claude-sonnet-5",
        "verdict": "",
        "seconds": 12.0,
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_tokens": 12500,
        "cache_write_tokens": 800,
        "cost_usd": 0.1731,
        "cost_partial": False,
    }


def test_attempts_sum_counts_and_last_attempt_wins_model() -> None:
    """A retried step bills twice; the model it finally ran as is the last."""
    rows = node_metrics([
        _entry("review", model="claude-sonnet-5", input_tokens=100,
               cost_usd=0.01, duration_ms=5000),
        _entry("review", model="claude-opus-5", input_tokens=200,
               cost_usd=0.04, duration_ms=7000),
    ])
    row = rows["review"]
    assert row["attempts"] == 2
    assert row["model"] == "claude-opus-5"
    assert row["input_tokens"] == 300
    assert row["cost_usd"] == 0.05
    assert row["seconds"] == 12.0


def test_verdict_read_off_outputs_when_contract_declares_nothing() -> None:
    rows = node_metrics([_entry("review", outputs={"verdict": "needs_work"})])
    assert rows["review"]["verdict"] == "needs_work"

    rows = node_metrics([_entry("review", outputs={"decision": "approve"})])
    assert rows["review"]["verdict"] == "approve"


def test_last_verdict_wins_and_a_blank_one_does_not_erase_it() -> None:
    rows = node_metrics([
        _entry("review", outputs={"verdict": "needs_work"}),
        _entry("review", outputs={}),
        _entry("review", outputs={"verdict": "pass"}),
    ])
    assert rows["review"]["verdict"] == "pass"


def test_seconds_fall_back_to_stamps_when_duration_ms_absent() -> None:
    rows = node_metrics([
        _entry("plan", started_at="2026-01-01T00:00:00+00:00",
               ended_at="2026-01-01T00:03:05+00:00")
    ])
    assert rows["plan"]["seconds"] == 185.0


def test_unparseable_stamps_and_junk_counts_contribute_zero() -> None:
    rows = node_metrics([
        _entry("plan", started_at="not-a-date", ended_at="also-not",
               input_tokens="lots", cost_usd=None)
    ])
    assert rows["plan"]["seconds"] == 0.0
    assert rows["plan"]["input_tokens"] == 0
    assert rows["plan"]["cost_usd"] == 0.0


def test_cost_partial_is_sticky_across_attempts() -> None:
    rows = node_metrics([
        _entry("plan", cost_usd=0.02),
        _entry("plan", cost_partial=True, input_tokens=90),
    ])
    assert rows["plan"]["cost_partial"] is True


def test_history_junk_is_skipped_not_raised() -> None:
    assert node_metrics([]) == {}
    assert node_metrics([None, "junk", 3, {}, {"step_id": ""}]) == {}


def test_totals_sum_every_numeric_column_and_carry_the_partial_flag() -> None:
    rows = [
        {"seconds": 12.0, "input_tokens": 100, "output_tokens": 10,
         "cache_read_tokens": 5, "cache_write_tokens": 1, "cost_usd": 0.10,
         "cost_partial": False},
        {"seconds": 3.5, "input_tokens": 200, "output_tokens": 20,
         "cache_read_tokens": 7, "cache_write_tokens": 2, "cost_usd": 0.0731,
         "cost_partial": True},
    ]
    assert metrics_totals(rows) == {
        "seconds": 15.5,
        "input_tokens": 300,
        "output_tokens": 30,
        "cache_read_tokens": 12,
        "cache_write_tokens": 3,
        "cost_usd": 0.1731,
        "cost_partial": True,
    }


def test_totals_of_pending_nodes_are_zero_not_missing() -> None:
    """Every node is a row, so a run before its first step still totals."""
    totals = metrics_totals([{"cost_partial": False}, {}])
    assert totals["cost_usd"] == 0.0
    assert totals["input_tokens"] == 0
    assert totals["cost_partial"] is False
