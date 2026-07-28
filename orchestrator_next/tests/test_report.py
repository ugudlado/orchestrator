"""ORC-115: widened workflow-report table + structured output fields."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from orchestrator_next import report as report_mod


def _render(step_history: list, issues: list | None = None) -> tuple[dict, str]:
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        result = report_mod.render_report(step_history, issues or [])
    finally:
        sys.stderr = old
    return result, buf.getvalue()


def test_agent_row_shows_split_tokens_model_and_structured_fields():
    history = [
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 1,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 250,
                "model": "sonnet-4-5",
                "cost_usd": 0.0123,
                "duration_ms": 4500,
            },
        }
    ]
    result, stderr = _render(history)
    assert "sonnet-4-5" in stderr
    assert "1,000" in stderr or "1000" in stderr
    assert "250" in stderr
    assert "4.5s" in stderr
    assert "$0.0123" in stderr
    step = result["steps"][0]
    assert step["input_tokens"] == 1000
    assert step["output_tokens"] == 250
    assert step["model"] == "sonnet-4-5"


def test_collapse_sums_tokens_cost_duration_last_model_wins():
    history = [
        {
            "step_id": "explore",
            "status": "failed",
            "attempt": 1,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "model": "haiku-3-5",
                "cost_usd": 0.001,
                "duration_ms": 1000,
            },
        },
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 2,
            "usage": {
                "input_tokens": 200,
                "output_tokens": 80,
                "model": "sonnet-4-5",
                "cost_usd": 0.004,
                "duration_ms": 2000,
            },
        },
    ]
    result, _stderr = _render(history)
    step = result["steps"][0]
    assert step["attempts"] == 2
    assert step["input_tokens"] == 300
    assert step["output_tokens"] == 130
    assert step["cost_usd"] == pytest.approx(0.005)
    assert step["duration_ms"] == 3000
    assert step["model"] == "sonnet-4-5"


def test_null_usage_renders_dashes_without_exception():
    history = [{"step_id": "script-step", "status": "completed", "attempt": 1, "usage": None}]
    result, stderr = _render(history)
    # Metric columns: Duration, Model, In, Out, Cost — all '—'
    # Header row + separator + data row; data row should contain dashes for metrics.
    data_lines = [ln for ln in stderr.splitlines() if ln.startswith("script-step")]
    assert data_lines, f"expected a data row in stderr:\n{stderr}"
    row = data_lines[0]
    assert row.count("—") >= 5
    assert result["steps"][0]["duration_ms"] == 0


def test_missing_model_and_cost_contribute_zero_to_totals():
    history = [
        {
            "step_id": "old-step",
            "status": "completed",
            "attempt": 1,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "duration_ms": 500,
            },
        }
    ]
    result, _stderr = _render(history)
    assert result["totals"]["cost_usd"] == 0.0
    assert result["totals"]["input_tokens"] == 10
    assert result["totals"]["output_tokens"] == 5


def test_cache_tokens_rendered_and_kept_disjoint_from_input():
    """Cache read/write drive most of the cost; In stays raw input only."""
    history = [
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 1,
            "usage": {
                "input_tokens": 13,
                "output_tokens": 3312,
                "cache_read_input_tokens": 517216,
                "cache_creation_input_tokens": 52507,
                "model": "sonnet-4-6",
                "cost_usd": 0.5199,
                "duration_ms": 81481,
            },
        }
    ]
    result, stderr = _render(history)
    assert "517,216" in stderr
    assert "52,507" in stderr
    step = result["steps"][0]
    assert step["input_tokens"] == 13  # disjoint: not inflated by cache
    assert step["cache_read_input_tokens"] == 517216
    assert step["cache_creation_input_tokens"] == 52507
    assert result["totals"]["cache_read_input_tokens"] == 517216
    assert result["totals"]["cache_creation_input_tokens"] == 52507


def test_cache_tokens_accumulate_across_attempts():
    history = [
        {
            "step_id": "explore",
            "status": "failed",
            "attempt": 1,
            "usage": {"cache_read_input_tokens": 100, "cache_creation_input_tokens": 10},
        },
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 2,
            "usage": {"cache_read_input_tokens": 200, "cache_creation_input_tokens": 20},
        },
    ]
    result, _stderr = _render(history)
    step = result["steps"][0]
    assert step["cache_read_input_tokens"] == 300
    assert step["cache_creation_input_tokens"] == 30


def test_tokens_field_is_the_grand_total_including_cache():
    """`tokens` counts every billed token, not just in+out. Nothing outside this
    module consumes it (structured output is console-only), so widening it is safe
    — and a `tokens` that omitted 98% of real volume was the bug being fixed."""
    history = [
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 1,
            "usage": {
                "input_tokens": 13,
                "output_tokens": 3312,
                "cache_read_input_tokens": 517216,
                "cache_creation_input_tokens": 52507,
            },
        }
    ]
    result, stderr = _render(history)
    expected = 13 + 3312 + 517216 + 52507
    assert result["steps"][0]["tokens"] == expected
    assert result["totals"]["tokens"] == expected
    assert "573,048" in stderr  # the all-tokens footer line


def test_missing_cache_fields_contribute_zero():
    """Old state files predating cache tracking must not crash or skew totals."""
    history = [
        {
            "step_id": "old-step",
            "status": "completed",
            "attempt": 1,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    ]
    result, _stderr = _render(history)
    assert result["totals"]["cache_read_input_tokens"] == 0
    assert result["totals"]["cache_creation_input_tokens"] == 0


def test_totals_include_input_and_output_token_sums():
    history = [
        {
            "step_id": "a",
            "status": "completed",
            "attempt": 1,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "model": "sonnet-4-5",
                "cost_usd": 0.01,
                "duration_ms": 1000,
            },
        },
        {
            "step_id": "b",
            "status": "completed",
            "attempt": 1,
            "usage": {
                "input_tokens": 50,
                "output_tokens": 30,
                "model": "haiku-3-5",
                "cost_usd": 0.002,
                "duration_ms": 500,
            },
        },
    ]
    result, _stderr = _render(history)
    assert result["totals"]["input_tokens"] == 150
    assert result["totals"]["output_tokens"] == 50
    assert isinstance(result["totals"]["input_tokens"], int)
    assert isinstance(result["totals"]["output_tokens"], int)


# ---------------------------------------------------------------------------
# ORC-116: Briefing column + structured briefing field
# ---------------------------------------------------------------------------


def test_briefing_appears_on_its_own_line_and_in_structured_output():
    """step_history entry with briefing appears in full under its step's row."""
    history = [
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 1,
            "briefing": "Implemented X",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "sonnet-4-5",
                "cost_usd": 0.001,
                "duration_ms": 1000,
            },
        }
    ]
    result, stderr = _render(history)
    assert "Implemented X" in stderr
    assert result["steps"][0]["briefings"] == [
        {"attempt": 1, "status": "completed", "briefing": "Implemented X"}
    ]


def test_missing_briefing_renders_nothing_and_empty_list_in_json():
    """step_history entry without briefing has no briefing line and an empty list in JSON."""
    history = [
        {
            "step_id": "script-step",
            "status": "completed",
            "attempt": 1,
            "usage": {"duration_ms": 100},
        }
    ]
    result, stderr = _render(history)
    assert "[completed]" not in stderr
    assert result["steps"][0]["briefings"] == []


def test_long_briefing_not_truncated():
    """briefing longer than the old 120-char cap renders in full."""
    raw = "A" * 150
    history = [
        {
            "step_id": "explore",
            "status": "completed",
            "attempt": 1,
            "briefing": raw,
            "usage": {"duration_ms": 100},
        }
    ]
    result, stderr = _render(history)
    assert raw in stderr
    assert result["steps"][0]["briefings"] == [
        {"attempt": 1, "status": "completed", "briefing": raw}
    ]


def test_each_attempt_briefing_survives_collapse():
    """Retried step: both the failed attempt's briefing and the fix's briefing
    must appear — not just the last one (regression: briefing collapse bug)."""
    history = [
        {
            "step_id": "design-review",
            "status": "failed",
            "attempt": 1,
            "briefing": "RED tasks fail their own verify gates",
            "usage": {"duration_ms": 100},
        },
        {
            "step_id": "design-review",
            "status": "completed",
            "attempt": 2,
            "briefing": "Fixed: RED tasks use test.todo()",
            "usage": {"duration_ms": 100},
        },
    ]
    result, stderr = _render(history)
    assert "RED tasks fail their own verify gates" in stderr
    assert "Fixed: RED tasks use test.todo()" in stderr
    assert len(result["steps"][0]["briefings"]) == 2


# ---------------------------------------------------------------------------
# Cross-workflow aggregation (`orchestrator report --all`)
# ---------------------------------------------------------------------------


def _write_state(tmp_path: Path, cid: str, entries: list[dict]) -> Path:
    import yaml

    d = tmp_path / ".orchestrator" / cid
    d.mkdir(parents=True, exist_ok=True)
    path = d / "state.yaml"
    path.write_text(yaml.safe_dump({"change_id": cid, "step_history": entries}))
    return path


def test_aggregate_math_across_workflows(tmp_path):
    a = _write_state(tmp_path, "feat-a", [
        {"step_id": "implement", "status": "failed", "attempt": 1,
         "usage": {"duration_ms": 1000, "cost_usd": 1.0}},
        {"step_id": "implement", "status": "completed", "attempt": 2,
         "usage": {"duration_ms": 3000, "cost_usd": 2.0}},
    ])
    b = _write_state(tmp_path, "feat-b", [
        {"step_id": "implement", "status": "completed", "attempt": 1,
         "usage": {"duration_ms": 2000, "cost_usd": 1.0}},
        {"step_id": "review", "status": "failed", "attempt": 1,
         "usage": {"duration_ms": 500, "cost_usd": 0.5}},
    ])
    agg = report_mod.aggregate([a, b])
    assert agg["workflows"] == 2
    by_id = {s["step_id"]: s for s in agg["steps"]}

    imp = by_id["implement"]
    assert imp["runs"] == 2
    # feat-a: 4000ms/$3 across attempts; feat-b: 2000ms/$1
    assert imp["avg_duration_ms"] == 3000
    assert imp["avg_cost_usd"] == 2.0
    assert imp["retry_rate"] == 0.5   # only feat-a needed attempt 2
    assert imp["failure_rate"] == 0.0  # both runs ended completed

    rev = by_id["review"]
    assert rev["runs"] == 1
    assert rev["failure_rate"] == 1.0
    assert agg["totals"]["cost_usd"] == 4.5


def test_find_state_files_covers_active_and_archive(tmp_path):
    active = _write_state(tmp_path, "feat-a", [])
    arch_dir = tmp_path / "spec" / "changes" / "archive" / "20260101-feat-z"
    arch_dir.mkdir(parents=True)
    import yaml
    archived = arch_dir / "state.yaml"
    archived.write_text(yaml.safe_dump({"change_id": "feat-z", "step_history": []}))
    files = report_mod.find_state_files(str(tmp_path))
    assert active in files and archived in files
