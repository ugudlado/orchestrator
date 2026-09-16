"""Shared helpers for workflow step list entries.

Workflow `steps:` entries may be:

- a plain string id (shell or agent step — contract.yaml decides which)
- ``{id: ...}`` with optional routing (``on_failure``, ``on_success``, ``max_retries``,
  ``depends_on``) and an optional ``requires: <token_name>`` gate binding
- ``{gate: <id>, show: [...], approve_as: <token_name>}`` — a signoff gate
  (protocol v2 §7). A gate has no contract file: the recipe entry *is* the
  contract, so ``show``/``approve_as`` ride along in the promoted plan node.
- legacy ``{prompt: <dir>, ...}`` — ``prompt:`` is a redundant fallback that
  only derives the id when no ``id:`` is given. It never selects the prompt:
  the step's ``contract.yaml`` (``steps/<id>/contract.yaml``) is the single
  source of truth for prompt/model, so plain ids are preferred.

Shell and agent steps are indistinguishable at the workflow level — both are
plain ids; the contract's ``run:`` vs ``prompt:`` decides dispatch.
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
    if entry.get("gate"):
        # `{gate: design-signoff, ...}` — the gate name doubles as the node id.
        return str(entry["gate"])
    if entry.get("prompt"):
        # Legacy fallback: prompt ref doubles as the id when no id is given.
        return str(entry["prompt"])
    if entry.get("include"):
        return str(entry["include"])
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


def is_gate_entry(entry: Any) -> bool:
    """True when a recipe step entry declares a signoff gate (protocol v2 §7)."""
    return isinstance(entry, dict) and bool(entry.get("gate"))
