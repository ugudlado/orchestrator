"""Tests for the mid-run running cost total (ORC-125).

Covers the pure `sum_cost_usd` / `format_cost_so_far` helpers, the
step-completion surfacing through the `orchestrator done` (record) CLI path,
and the `orchestrator next` action-dict path that injects
`estimated_cost_so_far`. No DuckDB — the total is re-derived from
step_history[].usage.cost_usd.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from orchestrator_next.pricing import format_cost_so_far, sum_cost_usd


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_ORCHESTRATOR = _REPO_ROOT / "bin" / "orchestrator"


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


# ---------------------------------------------------------------------------
# `orchestrator next` action-dict path — estimated_cost_so_far injection
# ---------------------------------------------------------------------------
#
# The `next` agent path (bin/orchestrator) attaches an additive
# `estimated_cost_so_far` field to the emitted action JSON, summed from
# step_history[].usage.cost_usd. These tests drive the real CLI as a
# subprocess (the same seam test_pre_stamp_idempotency uses) and parse the
# action JSON off stdout, so they exercise the production injection rather
# than re-implementing the sum.


def _write_stub_agent_contracts(tmp_path: Path) -> Path:
    """Stub step contracts so dispatch resolves an agent step (model set),
    which is the arm that injects estimated_cost_so_far."""
    contracts = tmp_path / "stub-steps"
    contracts.mkdir(exist_ok=True)
    for sid, model in (("explore", "sonnet"), ("design-and-draft-artifacts", "opus")):
        step_dir = contracts / sid
        step_dir.mkdir(exist_ok=True)
        (step_dir / "contract.yaml").write_text(yaml.safe_dump({
            "id": sid, "model": model, "instruction": f"do {sid}",
            "inputs": [], "outputs": [], "rules": [],
        }))
        (step_dir / "prompt.md").write_text(f"do {sid}")
    return contracts


def _run_next(tmp_path: Path, state_path: Path, contracts: Path) -> dict:
    """Run `orchestrator next` and return the parsed action JSON from stdout."""
    env = {
        **os.environ,
        "WORKFLOW_STATE_DIR": str(tmp_path),
        "ORCHESTRATOR_HOME": str(_REPO_ROOT),
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path),
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": str(contracts),
    }
    proc = subprocess.run(
        [sys.executable, str(_BIN_ORCHESTRATOR), "next", str(state_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return json.loads(proc.stdout)


def test_next_action_injects_estimated_cost_so_far(tmp_path):
    """AC-1: `orchestrator next` against a fixture whose step_history usage
    cost_usd values are 0.01 + 0.02 emits action JSON with
    estimated_cost_so_far == 0.03."""
    state = {
        "schema": "feature",
        "change_id": "cost-next",
        "slug": "cost-next",
        "status": "active",
        "repo_root": str(tmp_path),
        "phase": "specify",
        "workflow_plan": {
            "specify": {
                "nodes": [
                    {"id": "explore", "status": "completed", "agent": "discoverer",
                     "goal": "explore", "inputs": [], "outputs": [], "rules": []},
                    {"id": "design-and-draft-artifacts", "status": "pending",
                     "agent": "architect", "goal": "design", "inputs": [],
                     "outputs": [], "rules": []},
                ],
                "filtered": [],
            },
        },
        "step_history": [
            {"step_id": "explore", "phase": "specify", "status": "completed",
             "agent": "discoverer", "attempt": 1,
             "started_at": "2026-06-01T00:00:00Z", "ended_at": "2026-06-01T00:01:00Z",
             "usage": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01}},
            {"step_id": "explore", "phase": "specify", "status": "completed",
             "agent": "discoverer", "attempt": 2,
             "started_at": "2026-06-01T00:02:00Z", "ended_at": "2026-06-01T00:03:00Z",
             "usage": {"input_tokens": 200, "output_tokens": 20, "cost_usd": 0.02}},
        ],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state))
    contracts = _write_stub_agent_contracts(tmp_path)

    action = _run_next(tmp_path, state_path, contracts)

    # Dispatched the pending agent step, and injected the summed running total.
    assert action["step_id"] == "design-and-draft-artifacts"
    assert action["estimated_cost_so_far"] == pytest.approx(0.03)


def test_next_action_estimated_cost_zero_on_fresh_state(tmp_path):
    """AC-2: with no completed steps, `orchestrator next` emits
    estimated_cost_so_far == 0.0 without raising."""
    state = {
        "schema": "feature",
        "change_id": "cost-next-fresh",
        "slug": "cost-next-fresh",
        "status": "active",
        "repo_root": str(tmp_path),
        "phase": "specify",
        "workflow_plan": {
            "specify": {
                "nodes": [
                    {"id": "explore", "status": "pending", "agent": "discoverer",
                     "goal": "explore", "inputs": [], "outputs": [], "rules": []},
                ],
                "filtered": [],
            },
        },
        "step_history": [],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state))
    contracts = _write_stub_agent_contracts(tmp_path)

    action = _run_next(tmp_path, state_path, contracts)

    assert action["step_id"] == "explore"
    assert action["estimated_cost_so_far"] == pytest.approx(0.0)
