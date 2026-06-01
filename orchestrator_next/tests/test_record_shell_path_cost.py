"""T-7 RED: record.py shell-path cost + JSONL-branch inertness.

Pins ORC-111 AC-7 behaviour for shell-driver done payloads:
  1. Adapter usage with model + tokens but no cost_usd → priced via usage['model']
  2. Shell payload (no agent_task_result / agentId) → no JSONL enrichment or
     ~/.claude/projects reads (including at FEATURE boundary)
  3. Tool-reported cost_usd (claude/omp) preserved; _compute_cost_usd not invoked
"""
from __future__ import annotations

import builtins
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import orchestrator_next.record as record_mod
from orchestrator_next.record import record  # noqa: E402
from orchestrator_next.upsert import ensure_schema  # noqa: E402

_STUB_CONTRACT = textwrap.dedent("""\
    id: execute-step
    agent: developer
    instruction: Execute the step.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
""")


def _write_state(tmp_path, *, workflow_plan: dict | None = None) -> str:
    plan = workflow_plan or {
        "implement": {"active": ["execute-step", "trailing-step"], "filtered": []},
    }
    state = {
        "change_id": "orc-111-shell-cost",
        "phase": list(plan.keys())[-1],
        "repo_root": str(tmp_path / "repo"),
        "workflow_plan": plan,
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _shell_payload(
    *,
    usage: dict,
    step_id: str = "execute-step",
    phase: str = "implement",
    agent: str = "developer",
) -> dict:
    """Synthetic shell-driver done payload (no chat-driver fields)."""
    return {
        "step_id": step_id,
        "phase": phase,
        "status": "completed",
        "agent": agent,
        "outputs": {"task_execution_result": {"task_id": "T-1", "status": "completed"}},
        "usage": usage,
    }


def _recorded_usage(state_path: str) -> dict:
    with open(state_path) as f:
        state = yaml.safe_load(f)
    history = state.get("step_history") or []
    assert history, "step_history empty after record()"
    return history[-1].get("usage") or {}


@pytest.fixture(autouse=True)
def clear_pricing_cache():
    record_mod._pricing_cache.clear()
    yield
    record_mod._pricing_cache.clear()


@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


@pytest.fixture()
def contracts_dir(tmp_path, monkeypatch):
    d = tmp_path / "contracts"
    d.mkdir()
    (d / "execute-step.yaml").write_text(_STUB_CONTRACT)
    (d / "final-step.yaml").write_text(_STUB_CONTRACT.replace("execute-step", "final-step"))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    fn = getattr(record_mod, "_load_routes", None)
    if fn is not None and hasattr(fn, "cache_clear"):
        fn.cache_clear()
    return d


class TestShellPathTokensOnlyCosting:
    """Adapter usage without cost_usd is priced via usage['model']."""

    def test_computes_cost_from_usage_model_not_agent_route(
        self, tmp_path, in_memory_db, contracts_dir
    ):
        """Tokens-only tool: usage['model'] from adapter drives _compute_cost_usd."""
        state_path = _write_state(tmp_path)
        usage = {
            "input_tokens": 10000,
            "output_tokens": 2000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "model": "claude-opus-4-7",
        }
        payload = _shell_payload(usage=usage)

        result, exit_code = record(state_path, payload, db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        recorded = _recorded_usage(state_path)
        expected_opus = 10000 * 15.0 / 1_000_000 + 2000 * 75.0 / 1_000_000
        expected_sonnet = 10000 * 3.0 / 1_000_000 + 2000 * 15.0 / 1_000_000

        assert recorded.get("model") == "claude-opus-4-7"
        assert recorded.get("cost_usd") == pytest.approx(expected_opus, rel=1e-6)
        assert recorded.get("cost_usd") != pytest.approx(expected_sonnet, rel=1e-6)


class TestShellPathJsonlInertness:
    """Shell payloads must not touch ~/.claude or JSONL enrichment helpers."""

    def test_feature_boundary_shell_payload_no_jsonl_reads(
        self, tmp_path, in_memory_db, contracts_dir, monkeypatch
    ):
        """No agent_task_result/agentId → JSONL branches inert even at FEATURE boundary."""
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        repo = home / "code" / "feature_worktrees" / "orc-111"
        repo.mkdir(parents=True)

        from orchestrator_next.jsonl_usage import _repo_slug

        projects = home / ".claude" / "projects" / _repo_slug(str(repo))
        projects.mkdir(parents=True)
        (projects / "sess-shell.jsonl").write_text(
            '{"type":"assistant","message":{"model":"claude-opus-4-7",'
            '"usage":{"input_tokens":99999,"output_tokens":88888}}}\n',
            encoding="utf-8",
        )

        plan = {"implement": {"active": ["final-step"], "filtered": []}}
        state = {
            "change_id": "orc-111-shell-cost",
            "phase": "implement",
            "repo_root": str(repo),
            "workflow_plan": plan,
            "step_history": [],
        }
        state_path = str(tmp_path / "state.yaml")
        Path(state_path).write_text(yaml.safe_dump(state, sort_keys=False))

        payload = _shell_payload(
            step_id="final-step",
            usage={
                "input_tokens": 5000,
                "output_tokens": 1000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "model": "claude-sonnet-4-6",
            },
        )
        assert "agent_task_result" not in payload
        assert "agent_id" not in payload

        driver_calls: list[str] = []
        subagent_calls: list[str] = []
        enrichment_calls: list[str] = []
        claude_opens: list[str] = []

        real_open = builtins.open

        def guarded_open(file, *args, **kwargs):
            path_str = str(file)
            if ".claude/projects" in path_str.replace("\\", "/"):
                claude_opens.append(path_str)
            return real_open(file, *args, **kwargs)

        def track_driver(*args, **kwargs):
            driver_calls.append("_resolve_driver_session")
            return record_mod._resolve_driver_session(*args, **kwargs)

        def track_subagent(*args, **kwargs):
            subagent_calls.append("_resolve_subagent_rows")
            return record_mod._resolve_subagent_rows(*args, **kwargs)

        def track_extract_agent_usage(repo_root, agent_id):
            enrichment_calls.append(f"extract_agent_usage:{agent_id}")
            from orchestrator_next import jsonl_usage

            return jsonl_usage.extract_agent_usage(repo_root, agent_id)

        with patch("builtins.open", side_effect=guarded_open), \
             patch.object(record_mod, "_resolve_driver_session", side_effect=track_driver), \
             patch.object(record_mod, "_resolve_subagent_rows", side_effect=track_subagent), \
             patch(
                 "orchestrator_next.jsonl_usage.extract_agent_usage",
                 side_effect=track_extract_agent_usage,
             ):
            result, exit_code = record(state_path, payload, db=in_memory_db)

        assert exit_code == 0, f"record() failed: {result}"
        assert driver_calls == [], (
            f"_resolve_driver_session must not run for shell payload, was called {driver_calls}"
        )
        assert subagent_calls == [], (
            f"_resolve_subagent_rows must not run for shell payload, was called {subagent_calls}"
        )
        assert enrichment_calls == [], (
            f"agent_id JSONL enrichment must not run, was called {enrichment_calls}"
        )
        assert claude_opens == [], (
            f"~/.claude/projects must not be opened, got: {claude_opens}"
        )


class TestShellPathToolReportedCost:
    """When the adapter supplies cost_usd, record.py must preserve it."""

    @pytest.mark.parametrize(
        "tool_label,cost_usd",
        [("claude", 0.1958375), ("omp", 0.0025)],
    )
    def test_tool_cost_usd_preserved_compute_not_invoked(
        self, tmp_path, in_memory_db, contracts_dir, tool_label, cost_usd
    ):
        state_path = _write_state(tmp_path)
        usage = {
            "input_tokens": 5290,
            "output_tokens": 31,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 26978,
            "model": "claude-opus-4-8" if tool_label == "claude" else "gemini-2.0-flash",
            "cost_usd": cost_usd,
        }
        payload = _shell_payload(usage=usage)

        compute_calls: list[tuple] = []
        real_compute = record_mod._compute_cost_usd

        def tracking_compute(db, agent, usage_dict, **kwargs):
            compute_calls.append((agent, dict(usage_dict)))
            return real_compute(db, agent, usage_dict, **kwargs)

        with patch.object(record_mod, "_compute_cost_usd", side_effect=tracking_compute):
            result, exit_code = record(state_path, payload, db=in_memory_db)

        assert exit_code == 0, f"record() failed for {tool_label}: {result}"
        assert compute_calls == [], (
            f"_compute_cost_usd must not run when tool reports cost_usd ({tool_label})"
        )
        recorded = _recorded_usage(state_path)
        assert recorded.get("cost_usd") == pytest.approx(cost_usd, rel=1e-9)
