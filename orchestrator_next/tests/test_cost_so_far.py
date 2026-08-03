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

from orchestrator_next.pricing import (
    format_cost_so_far,
    format_last_step_usage,
    sum_cost_usd,
)


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
# Token-key contract between run_loop's zero floor and what the readers consume
# ---------------------------------------------------------------------------

def test_empty_usage_token_keys_are_the_ones_readers_consume():
    """run_loop._EMPTY_USAGE floors every recorded usage dict. Its token keys must be
    the names the adapters write and pricing/workflow-report read. They drifted once
    (cache_read_tokens vs cache_read_input_tokens): the floor's zeros sat in state
    beside the adapter's real counts, read by nobody. Cost was computed from the long
    form so nothing broke loudly — which is exactly why this needs a test."""
    from orchestrator_next.run_loop import _EMPTY_USAGE

    assert set(_EMPTY_USAGE) == {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
    # Every floored key is one _compute_cost_usd actually prices.
    priced = {
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    }
    assert set(_EMPTY_USAGE) <= priced
    # The floor must not fabricate a model or a cost for a failed/empty step.
    assert "cost_usd" not in _EMPTY_USAGE
    assert "model" not in _EMPTY_USAGE


# ---------------------------------------------------------------------------
# Per-step usage line (the ✓ step's own duration/tokens/cost)
# ---------------------------------------------------------------------------

def test_format_last_step_usage_renders_agent_step():
    """Cache keys must match what the adapters write and pricing reads —
    cache_read_input_tokens, NOT cache_read_tokens."""
    state = {"step_history": [
        {"usage": {"cost_usd": 0.10}},  # earlier step — must not be rendered
        {"usage": {
            "duration_ms": 9300,
            "input_tokens": 12100,
            "output_tokens": 834,
            "cache_read_input_tokens": 88200,
            "cache_creation_input_tokens": 1200,
            "cost_usd": 0.69,
        }},
    ]}
    line = format_last_step_usage(state)
    assert line == "9.3s  in=12.1k out=834 cache_r=88.2k cache_w=1.2k  $0.69"


def test_format_last_step_usage_script_step_duration_only():
    """Script steps record duration and no tokens/cost — still worth a line."""
    state = {"step_history": [{"usage": {"duration_ms": 1500}}]}
    assert format_last_step_usage(state) == "1.5s"


def test_format_last_step_usage_hides_trivial_durations():
    """A sub-100ms script step would render a useless '0.0s' — say nothing."""
    assert format_last_step_usage({"step_history": [{"usage": {"duration_ms": 12}}]}) == ""


def test_format_last_step_usage_no_misattribution_on_state_mutating_step():
    """archive-completed-change records itself PRE-script (it moves state.yaml, so a
    post-script record would crash), and that entry carries no usage. The usage line
    must stay empty rather than reprint the previous step's tokens under its name —
    format_cost_so_far was order-independent and immune to this; this one isn't."""
    state = {"step_history": [
        {"step_id": "ticket-done", "usage": {
            "input_tokens": 5000, "output_tokens": 900,
            "cache_read_input_tokens": 400000, "cost_usd": 0.42, "duration_ms": 30000,
        }},
        {"step_id": "archive-completed-change", "status": "completed", "outputs": {},
         "evidence": {"summary": "recorded pre-script (state-mutating inline step)"}},
    ]}
    assert format_last_step_usage(state) == ""


def test_format_last_step_usage_empty_when_nothing_recorded():
    assert format_last_step_usage({}) == ""
    assert format_last_step_usage({"step_history": []}) == ""
    assert format_last_step_usage({"step_history": [{}]}) == ""
    assert format_last_step_usage({"step_history": [{"usage": {}}]}) == ""
    assert format_last_step_usage({"step_history": ["not-a-dict"]}) == ""


def test_format_last_step_usage_tolerates_junk_values():
    state = {"step_history": [{"usage": {
        "duration_ms": None, "input_tokens": "junk",
        "output_tokens": 500, "cost_usd": "nan-ish",
    }}]}
    # Junk coerces to 0; the four token labels stay together so columns line up
    # across steps. Unparseable duration/cost drop out entirely.
    assert format_last_step_usage(state) == "in=0 out=500 cache_r=0 cache_w=0"


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
        "usage": {"cost_usd": 0.75},
    }

    env = {
        "PYTHONPATH": str(repo_root),
        "PATH": "/usr/bin:/bin",
        "ORCHESTRATOR_CONFIG": str(repo_root / "config"),
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
    for sid in ("explore", "design"):
        step_dir = contracts / sid
        step_dir.mkdir(exist_ok=True)
        (step_dir / "contract.yaml").write_text(yaml.safe_dump({
            "id": sid, "version": 1, "prompt": "prompt.md",
        }))
        (step_dir / "prompt.md").write_text(f"do {sid}")
    cfg = tmp_path / "orc-config"
    cfg.mkdir(exist_ok=True)
    (cfg / "models.yaml").write_text(yaml.safe_dump({
        "models": {
            "standard": {"tool": "claude", "model_id": "claude-sonnet-5"},
            "strong": {"tool": "claude", "model_id": "claude-opus-5"},
        },
        "step_models": {"explore": "standard", "design": "strong"},
    }))
    return contracts, cfg


def _run_next(tmp_path: Path, state_path: Path, contracts: Path, cfg: Path) -> dict:
    """Run `orchestrator next` and return the parsed action JSON from stdout."""
    env = {
        **os.environ,
        "WORKFLOW_STATE_DIR": str(tmp_path),
        "ORCHESTRATOR_HOME": str(_REPO_ROOT),
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path),
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": str(contracts),
        "ORCHESTRATOR_CONFIG": str(cfg),
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
                    {"id": "design", "status": "pending",
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
    contracts, cfg = _write_stub_agent_contracts(tmp_path)

    action = _run_next(tmp_path, state_path, contracts, cfg)

    # Dispatched the pending agent step, and injected the summed running total.
    assert action["step_id"] == "design"
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
    contracts, cfg = _write_stub_agent_contracts(tmp_path)

    action = _run_next(tmp_path, state_path, contracts, cfg)

    assert action["step_id"] == "explore"
    assert action["estimated_cost_so_far"] == pytest.approx(0.0)


def test_next_cli_emits_estimated_cost_so_far(tmp_path):
    """Integration: driving the real `orchestrator next` CLI against a fixture
    whose step_history usage cost_usd values are 0.25 + 0.02 emits both the
    additive action-JSON field (estimated_cost_so_far == 0.27) and the human
    `[cost so far: $0.27]` stderr line on the agent-dispatch path."""
    state = {
        "schema": "feature",
        "change_id": "cost-next-cli",
        "slug": "cost-next-cli",
        "status": "active",
        "repo_root": str(tmp_path),
        "phase": "specify",
        "workflow_plan": {
            "specify": {
                "nodes": [
                    {"id": "explore", "status": "completed", "agent": "discoverer",
                     "goal": "explore", "inputs": [], "outputs": [], "rules": []},
                    {"id": "design", "status": "pending",
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
             "usage": {"input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.25}},
            {"step_id": "explore", "phase": "specify", "status": "completed",
             "agent": "discoverer", "attempt": 2,
             "started_at": "2026-06-01T00:02:00Z", "ended_at": "2026-06-01T00:03:00Z",
             "usage": {"input_tokens": 200, "output_tokens": 20, "cost_usd": 0.02}},
        ],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state))
    contracts, cfg = _write_stub_agent_contracts(tmp_path)

    env = {
        **os.environ,
        "WORKFLOW_STATE_DIR": str(tmp_path),
        "ORCHESTRATOR_HOME": str(_REPO_ROOT),
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path),
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": str(contracts),
        "ORCHESTRATOR_CONFIG": str(cfg),
    }
    proc = subprocess.run(
        [sys.executable, str(_BIN_ORCHESTRATOR), "next", str(state_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    action = json.loads(proc.stdout)
    assert action["step_id"] == "design"
    assert action["estimated_cost_so_far"] == pytest.approx(0.27)
    # Human-readable running total surfaced on stderr for the self-driven caller.
    assert "[cost so far: $0.27]" in proc.stderr
