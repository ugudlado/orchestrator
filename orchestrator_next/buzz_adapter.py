"""Optional Buzz roster overlay for standalone orchestrator-next output.

The workflow engine remains model-driven and agent-agnostic. TeamLead enables
this adapter with ORCHESTRATOR_ROSTER=/path/to/roster.yaml; the adapter adds
the exact Buzz identity needed for ACP dispatch.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_HEX_PUBKEY = re.compile(r"^[0-9a-fA-F]{64}$")


class BuzzRosterError(ValueError):
    """A supplied Buzz roster cannot safely resolve the dispatched step."""


def enrich_action(action: dict[str, Any], roster_path: str) -> dict[str, Any]:
    """Return action with role, agent, and pinned agent_pubkey."""
    path = Path(roster_path)
    if not path.is_file():
        raise BuzzRosterError(f"Buzz roster not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BuzzRosterError(f"invalid Buzz roster {path}: {exc}") from exc

    steps = raw.get("steps") if isinstance(raw, dict) else None
    agents = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(steps, dict) or not isinstance(agents, dict):
        raise BuzzRosterError(f"Buzz roster {path} requires 'agents' and 'steps' mappings")

    step_id = str(action.get("step_id") or "")
    role = steps.get(step_id)
    if not isinstance(role, str) or not role.strip():
        raise BuzzRosterError(f"Buzz roster {path} has no role for step '{step_id}'")
    role = role.strip()

    binding = agents.get(role)
    if not isinstance(binding, dict):
        raise BuzzRosterError(f"Buzz roster {path} has no agent binding for role '{role}'")
    name = binding.get("name")
    pubkey = binding.get("pubkey")
    if not isinstance(name, str) or not name.strip():
        raise BuzzRosterError(f"Buzz roster role '{role}' requires a name")
    if not isinstance(pubkey, str) or not _HEX_PUBKEY.fullmatch(pubkey.strip()):
        raise BuzzRosterError(
            f"Buzz roster role '{role}' requires a 64-character hex pubkey"
        )

    return {
        **action,
        "role": role,
        "agent": name.strip(),
        "agent_pubkey": pubkey.strip().lower(),
    }
