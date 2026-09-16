"""Signoff gates and their tokens (docs/protocol-v2.md §7, plan Phase 3.1).

A gate is a recipe entry, not a step contract::

    - {gate: design-signoff, show: [design, tasks], approve_as: impl_token}
    - {id: implement, requires: impl_token}

Reaching the gate mints a token, parks the run at ``status: blocked``, and
returns a preview of the named artifacts. ``orchestrator approve <run>
<token>`` binds the token to its ``approve_as`` name, completes the gate node,
and lets any downstream step declaring ``requires: <that name>`` dispatch.

Token state lives under ``state.gates`` as a list of records::

    - token: <urlsafe>
      token_name: impl_token
      gate_id: design-signoff
      issued_at: 2026-09-17T…Z
      status: pending | approved | cancelled
      approved_at: …
      edits: {...}          # verbatim, whatever --edits carried

This module owns every read and write of that list; nothing else should reach
into ``state_raw["gates"]``.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GATE_KIND = "gate"
PREVIEW_HEAD_LINES = 40
TOKEN_BYTES = 16


class GateError(RuntimeError):
    """An approve/cancel call the engine refuses: unknown token, no gate open."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gate_records(state_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Every gate record on the run, oldest first. Never None."""
    raw = state_raw.get("gates")
    return [g for g in raw if isinstance(g, dict)] if isinstance(raw, list) else []


def node_is_gate(node: dict[str, Any]) -> bool:
    """True when a plan node was promoted from a ``{gate: ...}`` recipe entry."""
    return str(node.get("kind") or "") == GATE_KIND


def pending_gate(state_raw: dict[str, Any], gate_id: str) -> dict[str, Any] | None:
    """The open token for ``gate_id``, or None when the gate has never fired.

    Idempotency hinges on this: re-calling ``step`` at a blocked gate must
    return the token already issued, not mint a second one that would make the
    first approval a race.
    """
    for record in gate_records(state_raw):
        if record.get("gate_id") == gate_id and record.get("status") == "pending":
            return record
    return None


def approved_tokens(state_raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map ``token_name -> record`` for every approved gate on the run."""
    out: dict[str, dict[str, Any]] = {}
    for record in gate_records(state_raw):
        if record.get("status") == "approved" and record.get("token_name"):
            out[str(record["token_name"])] = record
    return out


def token_is_approved(state_raw: dict[str, Any], token_name: str) -> bool:
    """True when some gate bound to ``token_name`` has been approved."""
    return token_name in approved_tokens(state_raw)


def latest_approved_token(state_raw: dict[str, Any]) -> str | None:
    """The most recently approved token string, for ``status --json``."""
    approved = [g for g in gate_records(state_raw) if g.get("status") == "approved"]
    if not approved:
        return None
    approved.sort(key=lambda g: str(g.get("approved_at") or ""))
    return str(approved[-1].get("token") or "") or None


def issue_token(
    state_raw: dict[str, Any], gate_id: str, token_name: str
) -> dict[str, Any]:
    """Return the gate's pending record, minting one on first arrival.

    Mutates ``state_raw`` in place; the caller persists.
    """
    existing = pending_gate(state_raw, gate_id)
    if existing is not None:
        return existing
    record = {
        "token": secrets.token_urlsafe(TOKEN_BYTES),
        "token_name": token_name,
        "gate_id": gate_id,
        "issued_at": _utcnow(),
        "status": "pending",
    }
    state_raw.setdefault("gates", [])
    if not isinstance(state_raw["gates"], list):
        state_raw["gates"] = []
    state_raw["gates"].append(record)
    return record


def approve_token(
    state_raw: dict[str, Any], token: str, edits: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Approve the pending gate holding ``token``. Raises GateError otherwise.

    ``edits`` is recorded verbatim — the engine never interprets it. An already
    approved or cancelled token is refused rather than re-approved, so a replay
    cannot re-open a gate the run has moved past.
    """
    for record in gate_records(state_raw):
        if record.get("token") != token:
            continue
        if record.get("status") != "pending":
            raise GateError(
                f"token for gate {record.get('gate_id')!r} is "
                f"{record.get('status')!r}, not pending"
            )
        record["status"] = "approved"
        record["approved_at"] = _utcnow()
        if edits is not None:
            record["edits"] = edits
        return record
    raise GateError("unknown or expired gate token")


def preview(
    show: list[str], artifact_paths: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Render the ``show:`` artifacts a human needs to decide.

    Each entry is ``{path, sha256, head}`` — the first
    ``PREVIEW_HEAD_LINES`` lines, which is what a reviewer actually reads. A
    name with no known artifact, or a file that has not been written yet, still
    appears (with ``exists: false``) so the reviewer sees what is missing
    instead of a silently short preview.
    """
    import hashlib

    out: dict[str, dict[str, Any]] = {}
    for name in show:
        raw_path = artifact_paths.get(name)
        entry: dict[str, Any] = {"path": raw_path or "", "exists": False,
                                 "sha256": "", "head": ""}
        if raw_path:
            path = Path(raw_path)
            if path.is_file():
                data = path.read_bytes()
                text = data.decode("utf-8", errors="replace")
                entry.update(
                    exists=True,
                    sha256=hashlib.sha256(data).hexdigest(),
                    head="\n".join(text.splitlines()[:PREVIEW_HEAD_LINES]),
                )
        out[name] = entry
    return out
