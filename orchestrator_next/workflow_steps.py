"""Shared helpers for workflow step list entries.

Workflow `steps:` entries may be:

- a plain string id (shell or contract-backed step)
- ``{id: ...}`` with optional routing (``on_failure``, …)
- ``{skill: <name>, ...}`` — reusable installed skill (id defaults to skill name)
- ``{prompt: <path>, id: <id>, ...}`` — local prompt file (id required)

Exactly one of ``skill`` / ``prompt`` may appear on a dict entry (besides routing
keys). Shell steps stay plain ids pointing at ``run:`` contracts.
"""
from __future__ import annotations

from typing import Any


def step_id_of(entry: Any) -> str | None:
    """Return the step id for a workflow step entry, or None if unusable."""
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return None
        # Support legacy "id if flag" forms: take the leading token.
        return text.split()[0]
    if not isinstance(entry, dict):
        return None
    if entry.get("id"):
        return str(entry["id"])
    if entry.get("skill"):
        return str(entry["skill"])
    if entry.get("include"):
        return str(entry["include"])
    return None


def step_skill_of(entry: Any) -> str | None:
    """Return skill name if this entry declares ``skill:``, else None."""
    if isinstance(entry, dict) and entry.get("skill"):
        return str(entry["skill"])
    return None


def step_prompt_of(entry: Any) -> str | None:
    """Return prompt path if this entry declares ``prompt:``, else None."""
    if isinstance(entry, dict) and entry.get("prompt"):
        return str(entry["prompt"])
    return None


def normalize_step_entry(entry: Any) -> dict[str, Any]:
    """Normalize a workflow step entry to a dict with at least ``id``."""
    if isinstance(entry, str):
        sid = step_id_of(entry)
        return {"id": sid} if sid else {}
    if isinstance(entry, dict):
        out = dict(entry)
        sid = step_id_of(entry)
        if sid and "id" not in out:
            out["id"] = sid
        return out
    return {}
