"""T-1 RED: tests for usage_adapters.split_stdout.

Fixtures mirror verified stdout shapes from .tmp/cost-adapter-findings.md
(2026-06-01 live binary captures). These tests fail until
orchestrator_next/usage_adapters.py exists (T-2).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

FIXTURES = Path(__file__).parent / "fixtures" / "usage"


def split_stdout(tool: str, stdout_path: os.PathLike | str, *, route_model: str | None = None):
    """Lazy import so RED phase collects tests and fails at execution, not collection."""
    from orchestrator_next.usage_adapters import split_stdout as _split_stdout

    return _split_stdout(tool, stdout_path, route_model=route_model)

COMPLETION_SNIPPET = "COMPLETION:"
ROUTE_MODEL = "claude-sonnet-4-6"

_ZEROED_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "model": None,
    "cost_usd": None,
}


def _fixture(name: str) -> Path:
    return FIXTURES / name


def _assert_normalized_keys(result: dict) -> None:
    expected = {
        "assistant_text",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "model",
        "cost_usd",
    }
    assert set(result) == expected


class TestClaudeAdapter:
    def test_claude_normalizes_snake_case_usage_and_cost(self):
        result = split_stdout("claude", _fixture("claude.json"))

        _assert_normalized_keys(result)
        assert result["input_tokens"] == 5290
        assert result["output_tokens"] == 31
        assert result["cache_creation_input_tokens"] == 26978
        assert result["cache_read_input_tokens"] == 0
        assert result["model"] == "claude-opus-4-8"
        assert result["cost_usd"] == pytest.approx(0.1958375)

    def test_claude_assistant_text_contains_completion_block(self):
        result = split_stdout("claude", _fixture("claude.json"))
        assert COMPLETION_SNIPPET in result["assistant_text"]
        assert "task_execution_result: done" in result["assistant_text"]


class TestPiAdapter:
    def test_pi_jsonl_last_turn_end_usage_and_cost(self):
        result = split_stdout("pi", _fixture("pi.jsonl"))

        _assert_normalized_keys(result)
        assert result["input_tokens"] == 53064
        assert result["output_tokens"] == 23
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_creation_input_tokens"] == 0
        assert result["model"] == "gemini-2.0-flash"
        assert result["cost_usd"] == pytest.approx(0.0053156)

    def test_pi_assistant_text_from_turn_end_only(self):
        # message_start repeats the assistant message; only turn_end is read,
        # so the COMPLETION block must appear exactly once.
        result = split_stdout("pi", _fixture("pi.jsonl"))
        assert result["assistant_text"].count(COMPLETION_SNIPPET) == 1
        assert "task_execution_result: done" in result["assistant_text"]


class TestCursorAgentAdapter:
    def test_cursor_agent_camel_case_tokens_no_cost_route_model(self):
        result = split_stdout(
            "cursor-agent",
            _fixture("cursor_agent.json"),
            route_model=ROUTE_MODEL,
        )

        _assert_normalized_keys(result)
        assert result["input_tokens"] == 24527
        assert result["output_tokens"] == 65
        assert result["cache_read_input_tokens"] == 5312
        assert result["cache_creation_input_tokens"] == 0
        assert result["cost_usd"] is None
        assert result["model"] == ROUTE_MODEL

    def test_cursor_agent_assistant_text_contains_completion_block(self):
        result = split_stdout(
            "cursor-agent",
            _fixture("cursor_agent.json"),
            route_model=ROUTE_MODEL,
        )
        assert COMPLETION_SNIPPET in result["assistant_text"]
        assert "task_execution_result: done" in result["assistant_text"]


class TestCodexAdapter:
    def test_codex_jsonl_last_turn_completed_tokens_route_model(self):
        result = split_stdout(
            "codex",
            _fixture("codex.jsonl"),
            route_model=ROUTE_MODEL,
        )

        _assert_normalized_keys(result)
        assert result["input_tokens"] == 8420
        assert result["output_tokens"] == 128
        assert result["cache_read_input_tokens"] == 4096
        assert result["cache_creation_input_tokens"] == 0
        assert result["cost_usd"] is None
        assert result["model"] == ROUTE_MODEL

    def test_codex_assistant_text_contains_completion_block(self):
        result = split_stdout(
            "codex",
            _fixture("codex.jsonl"),
            route_model=ROUTE_MODEL,
        )
        assert COMPLETION_SNIPPET in result["assistant_text"]
        assert "task_execution_result: done" in result["assistant_text"]


class TestPassthrough:
    def test_unregistered_tool_returns_raw_stdout_zeroed_usage(self, tmp_path):
        stdout = tmp_path / "pi.out"
        raw = "plain agent reply\n\nCOMPLETION:\n  status: completed\n"
        stdout.write_text(raw, encoding="utf-8")

        result = split_stdout("pi", stdout)

        _assert_normalized_keys(result)
        assert result["assistant_text"] == raw
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_creation_input_tokens"] == 0
        assert result["model"] is None
        assert result["cost_usd"] is None


class TestErrorHandling:
    def test_missing_file_zeroed_usage_stderr_warning_no_exception(
        self, tmp_path, capsys
    ):
        missing = tmp_path / "does-not-exist.json"

        result = split_stdout("claude", missing)

        _assert_normalized_keys(result)
        assert result["assistant_text"] == ""
        for key, value in _ZEROED_USAGE.items():
            assert result[key] == value
        assert capsys.readouterr().err

    def test_malformed_json_zeroed_usage_raw_text_stderr_warning_no_exception(
        self, tmp_path, capsys
    ):
        bad = tmp_path / "bad.json"
        raw = "{not valid json at all"
        bad.write_text(raw, encoding="utf-8")

        result = split_stdout("claude", bad)

        _assert_normalized_keys(result)
        assert result["assistant_text"] == raw
        for key, value in _ZEROED_USAGE.items():
            assert result[key] == value
        assert capsys.readouterr().err
