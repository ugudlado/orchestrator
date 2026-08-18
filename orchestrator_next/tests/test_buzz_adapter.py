from __future__ import annotations

import yaml

from orchestrator_next.buzz_adapter import BuzzRosterError, enrich_action


def test_enrich_action_adds_pinned_buzz_identity(tmp_path):
    roster = tmp_path / "roster.yaml"
    roster.write_text(yaml.safe_dump({
        "version": 1,
        "agents": {
            "explorer": {"name": "Explorer", "pubkey": "A" * 64},
        },
        "steps": {"explore": "explorer"},
    }))

    action = enrich_action({"step_id": "explore", "model": "standard"}, str(roster))

    assert action == {
        "step_id": "explore",
        "model": "standard",
        "role": "explorer",
        "agent": "Explorer",
        "agent_pubkey": "a" * 64,
    }


def test_enrich_action_rejects_unpinned_role(tmp_path):
    roster = tmp_path / "roster.yaml"
    roster.write_text(yaml.safe_dump({
        "version": 1,
        "agents": {"explorer": {"name": "Explorer"}},
        "steps": {"explore": "explorer"},
    }))

    try:
        enrich_action({"step_id": "explore"}, str(roster))
    except BuzzRosterError as exc:
        assert "64-character hex pubkey" in str(exc)
    else:
        raise AssertionError("an unpinned Buzz roster must fail")
