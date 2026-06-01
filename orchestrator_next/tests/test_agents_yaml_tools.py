"""Assert config/agents.yaml tool wiring for vendor-agnostic usage capture.

Loads config/agents.yaml via PyYAML and pins the four tool-config corrections
required by ORC-111 (cursor binary, JSON stdout flags, omp entry).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_HERE = Path(__file__).parent.resolve()
_REPO_ROOT = _HERE.parents[1]
_AGENTS_YAML = _REPO_ROOT / "config" / "agents.yaml"

@pytest.fixture(scope="module")
def tools() -> dict:
    data = yaml.safe_load(_AGENTS_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "tools" in data
    return data["tools"]


def _args(tools: dict, name: str) -> list[str]:
    return list(tools[name]["args_template"])


def _contains_subsequence(haystack: list[str], needle: list[str]) -> bool:
    """Return True if needle appears contiguously in haystack."""
    if not needle:
        return True
    n = len(needle)
    for i in range(len(haystack) - n + 1):
        if haystack[i : i + n] == needle:
            return True
    return False


class TestAgentsYamlToolWiring:
    """Pin shell-driver tool entries for structured stdout usage capture."""

    def test_cursor_uses_cursor_agent_with_json_output(self, tools: dict) -> None:
        cursor = tools["cursor"]
        assert cursor["binary"] == "cursor-agent"
        args = _args(tools, "cursor")
        assert "-p" in args
        assert _contains_subsequence(args, ["--output-format", "json"])

    def test_claude_args_include_json_output_format(self, tools: dict) -> None:
        args = _args(tools, "claude")
        assert _contains_subsequence(args, ["--output-format", "json"])

    def test_codex_exec_with_json_flag(self, tools: dict) -> None:
        args = _args(tools, "codex")
        assert args[0] == "exec"
        assert "--json" in args

    def test_omp_tool_entry_with_model(self, tools: dict) -> None:
        assert "omp" in tools
        omp = tools["omp"]
        assert omp["binary"] == "omp"
        args = _args(tools, "omp")
        has_json_mode = "--mode=json" in args or (
            "--mode" in args and "json" in args
        )
        assert has_json_mode
        assert "--model" in args
        model_idx = args.index("--model")
        assert model_idx + 1 < len(args)
        assert args[model_idx + 1].strip() != ""
