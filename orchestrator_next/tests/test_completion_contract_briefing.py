"""COMPLETION contract requires outputs.reason; briefing is removed."""
from __future__ import annotations

from orchestrator_next.run_loop import _COMPLETION_CONTRACT


def test_completion_contract_contains_reason_substring():
    assert "reason" in _COMPLETION_CONTRACT.lower()


def test_completion_contract_has_reason_key_under_outputs():
    assert "reason:" in _COMPLETION_CONTRACT
    lower = _COMPLETION_CONTRACT.lower()
    assert "outputs:" in lower
    outputs_idx = lower.index("outputs:")
    assert "reason:" in lower[outputs_idx:]


def test_completion_contract_has_no_briefing():
    assert "briefing" not in _COMPLETION_CONTRACT.lower()
