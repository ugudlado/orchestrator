"""ORC-115: widened workflow-report table + structured output fields."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_STEP_DIR = Path(__file__).resolve().parent
if str(_STEP_DIR) not in sys.path:
    sys.path.insert(0, str(_STEP_DIR))

import workflow_report_step  # noqa: E402


def _render(step_history: list, issues: list | None = None) -> tuple[dict, str]:
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        result = workflow_report_step._render_report(step_history, issues or [])
    finally:
        sys.stderr = old
    return result, buf.getvalue()


@pytest.mark.xfail(strict=False, reason="T-4: widened table not yet implemented")
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


@pytest.mark.xfail(strict=False, reason="T-4: widened table not yet implemented")
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


@pytest.mark.xfail(strict=False, reason="T-4: widened table not yet implemented")
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


@pytest.mark.xfail(strict=False, reason="T-4: widened table not yet implemented")
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


@pytest.mark.xfail(strict=False, reason="T-4: widened table not yet implemented")
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
