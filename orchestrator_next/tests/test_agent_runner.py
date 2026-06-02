"""Tests for agent runner primitives."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=False)
def test_parse_completion_importable():
    from orchestrator_next.parse_completion import parse_completion

    assert callable(parse_completion)


@pytest.mark.xfail(strict=False)
def test_parse_completion_valid_block():
    from orchestrator_next.parse_completion import parse_completion

    text = """
Agent output...

COMPLETION:
  status: completed
  outputs:
    implementation_result: completed
"""
    parsed = parse_completion(text)
    assert parsed["status"] == "completed"
    assert parsed["outputs"]["implementation_result"] == "completed"


@pytest.mark.parametrize(
    "text",
    [
        "no completion block here",
        "COMPLETION:\n  status: blocked\n  outputs: {}\n",
        "COMPLETION:\n  status: completed\n  outputs: {bad\n",
    ],
)
@pytest.mark.xfail(strict=False)
def test_parse_completion_invalid_inputs(text: str):
    from orchestrator_next.parse_completion import parse_completion

    with pytest.raises(ValueError):
        parse_completion(text)
