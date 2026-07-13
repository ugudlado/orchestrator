"""ORC-116: _COMPLETION_CONTRACT must instruct agents to emit briefing."""
from __future__ import annotations

from orchestrator_next.run_loop import _COMPLETION_CONTRACT


def test_completion_contract_contains_briefing_substring():
    """_COMPLETION_CONTRACT string contains the substring 'briefing' (case-insensitive)."""
    assert "briefing" in _COMPLETION_CONTRACT.lower()


def test_completion_contract_has_briefing_key_under_outputs():
    """_COMPLETION_CONTRACT contains a 'briefing:' key example under an outputs block."""
    assert "briefing:" in _COMPLETION_CONTRACT
    # briefing: must appear after an outputs: line somewhere in the contract
    lower = _COMPLETION_CONTRACT.lower()
    assert "outputs:" in lower
    outputs_idx = lower.index("outputs:")
    assert "briefing:" in lower[outputs_idx:]
