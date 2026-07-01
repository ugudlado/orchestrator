"""Tests for the mid-run running cost total (ORC-125).

Covers the pure `sum_cost_usd` / `format_cost_so_far` helpers plus the
step-completion surfacing through the `orchestrator done` (record) CLI path.
No DuckDB — the total is re-derived from step_history[].usage.cost_usd.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from orchestrator_next.pricing import format_cost_so_far, sum_cost_usd


# ---------------------------------------------------------------------------
# sum_cost_usd — pure helper
# ---------------------------------------------------------------------------

def test_sum_multi_step_state():
    state = {
        "step_history": [
            {"step_id": "a", "usage": {"cost_usd": 0.01}},
            {"step_id": "b", "usage": {"cost_usd": 0.02}},
            {"step_id": "c", "usage": {"cost_usd": 1.5}},
        ]
    }
    assert sum_cost_usd(state) == pytest.approx(1.53)


def test_missing_usage_and_cost_keys():
    state = {
        "step_history": [
            {"step_id": "a"},                       # no usage at all
            {"step_id": "b", "usage": {}},          # usage without cost_usd
            {"step_id": "c", "usage": {"cost_usd": 0.05}},
        ]
    }
    assert sum_cost_usd(state) == pytest.approx(0.05)


def test_none_and_non_numeric_cost_contribute_zero():
    state = {
        "step_history": [
            {"usage": {"cost_usd": None}},
            {"usage": {"cost_usd": "not-a-number"}},
            {"usage": {"cost_usd": 0.10}},
            "not-a-dict-entry",
        ]
    }
    assert sum_cost_usd(state) == pytest.approx(0.10)


def test_empty_and_none_step_history():
    assert sum_cost_usd({}) == 0.0
    assert sum_cost_usd({"step_history": []}) == 0.0
    assert sum_cost_usd({"step_history": None}) == 0.0


def test_format_cost_so_far_two_decimals():
    state = {"step_history": [{"usage": {"cost_usd": 12.3}}]}
    assert format_cost_so_far(state) == "[cost so far: $12.30]"
    assert format_cost_so_far({}) == "[cost so far: $0.00]"


# ---------------------------------------------------------------------------
# Mid-run surfacing through `orchestrator done` (record CLI path)
# ---------------------------------------------------------------------------

_MULTI_STEP_STATE = textwrap.dedent(
    """\
    change_id: cost-so-far-int
    schema: feature
    version: 1
    status: active
    phase: implement
    repo_root: {repo}
    worktree_path: {repo}
    workflow_plan:
      implement:
        nodes:
          - id: step-one
            status: completed
          - id: step-two
            status: active
    step_history:
      - step_id: step-one
        phase: implement
        status: completed
        attempt: 1
        started_at: "2026-06-01T10:00:00Z"
        ended_at: "2026-06-01T10:05:00Z"
        usage:
          cost_usd: 0.25
          model: "test-model"
    """
)


def test_done_cli_emits_cost_so_far(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    state_path = tmp_path / "state.yaml"
    state_path.write_text(_MULTI_STEP_STATE.format(repo=str(tmp_path)))

    payload = {
        "step_id": "step-two",
        "phase": "implement",
        "status": "completed",
        "outputs": {},
        "evidence": {"summary": "done"},
        "usage": {"cost_usd": 0.75, "model": "test-model"},
    }

    env = {
        "PYTHONPATH": str(repo_root),
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator_next.record", str(state_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    # Mid-run running total = 0.25 (recorded) + 0.75 (this step) = 1.00
    assert "[cost so far: $1.00]" in proc.stderr
