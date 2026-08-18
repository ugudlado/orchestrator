"""End-to-end tests for the ACP transport, against a real ACP agent subprocess.

`fake_acp_agent.py` is a genuine ACP agent built on the same SDK the real ones
use, so these exercise the actual JSON-RPC handshake, streaming session updates,
permission round-trips, and usage reporting — with no API key and no network.

The contract under test is: whatever the agent does, `run_turn` returns the same
`NormalizedResult` shape `split_stdout()` returns, so `run_agent_step` ->
`parse_completion` -> `record` never learns which transport ran.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from orchestrator_next import acp_client
from orchestrator_next.parse_completion import parse_completion
from orchestrator_next.usage_adapters import ZEROED_USAGE

FAKE_AGENT = Path(__file__).with_name("fake_acp_agent.py")
USAGE_KEYS = set(ZEROED_USAGE)


def run(**env: str):
    """Run one turn against the fake agent with `env` overrides."""
    return acp_client.run_turn(
        command=sys.executable,
        args=[str(FAKE_AGENT)],
        prompt="do the thing",
        cwd=str(Path(__file__).parent),
        model_id=env.pop("_model", ""),
        env=env,
        timeout_s=int(env.pop("_timeout", 60)),
    )


# --------------------------------------------------------------- happy path
def test_returns_assistant_text_reassembled_from_chunks():
    result = run(FAKE_ACP_TEXT="hello from a streaming agent, chunked into pieces")
    assert result["assistant_text"] == "hello from a streaming agent, chunked into pieces"


def test_result_is_shape_compatible_with_split_stdout():
    """The whole point of the transport: downstream code cannot tell the difference."""
    result = run()
    assert "assistant_text" in result
    assert USAGE_KEYS.issubset(result), f"missing usage keys: {USAGE_KEYS - set(result)}"


def test_completion_block_parses_through_the_existing_parser():
    result = run()
    completion = parse_completion(result["assistant_text"])
    assert completion["status"] == "completed"
    assert completion["outputs"]["reason"] == "fake agent finished"


def test_stop_reason_is_recorded():
    assert run()["acp"]["stop_reason"] == "end_turn"


# -------------------------------------------------------------------- usage
def test_usage_maps_one_to_one_onto_orchestrator_token_classes():
    result = run(FAKE_ACP_USAGE="1")
    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 250
    assert result["cache_read_input_tokens"] == 200
    assert result["cache_creation_input_tokens"] == 50


def test_agent_reporting_no_usage_yields_zeros_not_a_crash():
    """PromptResponse.usage is UNSTABLE; agents may omit it. Degrade, never fail."""
    result = run()
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["assistant_text"], "text must still come through"


def test_model_is_reported_back_from_the_route_when_agent_is_silent():
    assert run(_model="claude-opus-5")["model"] == "claude-opus-5"


# -------------------------------------------------------------- permissions
def test_permission_request_is_auto_allowed_by_default():
    """Matches the pre-ACP `--force` / `--yolo` argv templates, now explicit."""
    result = run(FAKE_ACP_ASK="1")
    assert "[permission selected:y]" in result["assistant_text"]
    assert result["acp"]["permissions"] == [
        {"tool": "rm -rf build/", "policy": "allow", "option": "allow_once"}
    ]


# NOTE: the policy is a CLIENT-side decision, so it is read from orchestrator's
# own environment -- not forwarded to the agent subprocess. monkeypatch, not the
# `env=` kwarg. (Getting this wrong is a silent no-op: the agent happily ignores
# an env var it does not read, and the client keeps its default.)
def test_permission_policy_allow_always_picks_the_always_option(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_ACP_PERMISSION", "allow_always")
    result = run(FAKE_ACP_ASK="1")
    assert "[permission selected:Y]" in result["assistant_text"]


def test_permission_policy_reject_picks_the_reject_option(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_ACP_PERMISSION", "reject")
    result = run(FAKE_ACP_ASK="1")
    assert "[permission selected:n]" in result["assistant_text"]
    assert result["acp"]["permissions"][0]["option"] == "reject_once"


def test_unknown_permission_policy_falls_back_to_allow(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_ACP_PERMISSION", "banana")
    result = run(FAKE_ACP_ASK="1")
    assert "[permission selected:y]" in result["assistant_text"]


# --------------------------------------------------------------------- plan
def test_agent_plan_is_captured_as_evidence():
    result = run(FAKE_ACP_PLAN="1")
    assert result["acp"]["plan"] == [
        {"content": "read the spec", "status": "completed", "priority": "high"},
        {"content": "write the code", "status": "in_progress", "priority": "high"},
    ]


# ----------------------------------------------------------- failure policy
def test_crashed_agent_returns_a_retryable_zeroed_result_not_an_exception():
    """`record._is_spawn_failure` counts model=None + zero tokens toward the cap.

    A raised exception here would abort the whole workflow instead of letting
    on_failure/max_retries do their job.
    """
    result = run(FAKE_ACP_CRASH="1")
    assert result["assistant_text"] == ""
    assert result["input_tokens"] == 0
    assert result["model"] is None
    assert result["acp"]["stop_reason"] == "error"


def test_hanging_agent_times_out_into_a_retryable_result():
    result = run(FAKE_ACP_HANG="30", _timeout="2")
    assert result["assistant_text"] == ""
    assert result["acp"]["stop_reason"] == "timeout"
    assert USAGE_KEYS.issubset(result)


def test_agent_without_model_config_still_runs():
    """Model selection is a per-agent config option, not a protocol field."""
    result = run(FAKE_ACP_REJECT_CONFIG="1", _model="some-model")
    assert result["assistant_text"], "a rejected config option must not kill the turn"
    assert result["model"] == "some-model"


def test_missing_binary_raises_immediately():
    """Config errors are loud; agent faults are retryable. Different classes."""
    with pytest.raises(FileNotFoundError, match="not found on PATH"):
        acp_client.run_turn(
            command="definitely-not-a-real-agent-binary",
            prompt="x",
        )


# --------------------------------------------------- adapter-layer deletion
def test_one_transport_replaces_every_per_tool_dialect():
    """Regression guard for the point of the change.

    usage_adapters has a bespoke parser per CLI. Under ACP, the same client
    code handles any agent — so an agent that streams differently, reports
    usage differently, or asks for permission mid-turn all land in one shape.
    """
    variants = [
        {},
        {"FAKE_ACP_USAGE": "1"},
        {"FAKE_ACP_PLAN": "1"},
        {"FAKE_ACP_ASK": "1", "FAKE_ACP_USAGE": "1"},
    ]
    for env in variants:
        result = run(**env)
        assert USAGE_KEYS.issubset(result), env
        assert isinstance(result["assistant_text"], str), env
        assert parse_completion(result["assistant_text"])["status"] == "completed", env
