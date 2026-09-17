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


def cancel_pending(state_raw: dict[str, Any], gate_id: str) -> bool:
    """Withdraw ``gate_id``'s open token. True when one was actually cancelled.

    Used when a gate that already minted turns out to guard work a later
    review rejected: the outstanding token would otherwise stay approvable.
    Cancelling is safe because ``issue_token`` mints a fresh record once the
    gate is trustworthy again.
    """
    record = pending_gate(state_raw, gate_id)
    if record is None:
        return False
    record["status"] = "cancelled"
    record["cancelled_at"] = _utcnow()
    return True


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


def provenance(
    state_raw: dict[str, Any], show: list[str]
) -> dict[str, dict[str, Any]]:
    """Who stands behind each ``show:`` artifact.

    For every named artifact this returns the node that declared it as an
    output (``produced_by``) with that node's ``producer_status`` and
    ``attempts``, the *last* node that actually wrote it (``written_by`` — a
    reviewer whose contract declares ``out: <the thing it reviews>`` counts,
    which is how a rejected design came to be authored by its own reviewer),
    and ``last_verdict``, the most recent verdict any node recorded while
    producing it.

    Read-only: everything comes from the plan and step_history already on the
    run, including the per-node artifact hashes the engine records.
    """
    wanted = set(show)
    out: dict[str, dict[str, Any]] = {
        name: {"produced_by": "", "producer_status": "", "attempts": 0,
               "written_by": "", "last_verdict": ""}
        for name in show
    }

    plan = state_raw.get("workflow_plan") or {}
    by_node: dict[str, dict[str, Any]] = {}
    for phase_block in plan.values():
        if not isinstance(phase_block, dict):
            continue
        for node in phase_block.get("nodes") or []:
            if isinstance(node, dict) and node.get("id"):
                by_node[str(node["id"])] = node
            for rec in (node or {}).get("artifacts") or []:
                if not isinstance(rec, dict):
                    continue
                name = str(rec.get("name") or "")
                if name in wanted:
                    entry = out[name]
                    entry["produced_by"] = str(node.get("id") or "")
                    entry["producer_status"] = str(node.get("status") or "")
                    entry["attempts"] = int(node.get("attempts") or 0) or 1

    # step_history is append-only and in order, so the last writer wins.
    for hist in state_raw.get("step_history") or []:
        if not isinstance(hist, dict):
            continue
        step_id = str(hist.get("step_id") or "")
        verdict = str(((hist.get("outputs") or {}) if isinstance(
            hist.get("outputs"), dict) else {}).get("verdict") or "")
        for rec in hist.get("artifacts") or []:
            if not isinstance(rec, dict):
                continue
            name = str(rec.get("name") or "")
            if name not in wanted:
                continue
            entry = out[name]
            entry["written_by"] = step_id
            # A re-review that passed must clear an earlier rejection, so this
            # overwrites rather than accumulating.
            entry["last_verdict"] = verdict
            if not entry["produced_by"]:
                entry["produced_by"] = step_id
                node = by_node.get(step_id) or {}
                entry["producer_status"] = str(
                    node.get("status") or hist.get("status") or "")
                entry["attempts"] = int(hist.get("attempt") or 0) or 1

    for name in show:
        entry = out[name]
        if not entry["produced_by"]:
            continue
        node = by_node.get(entry["produced_by"]) or {}
        if node.get("status"):
            entry["producer_status"] = str(node["status"])
        if node.get("attempts"):
            entry["attempts"] = int(node["attempts"])
    return out


def untrusted(
    prov: dict[str, dict[str, Any]], fail_verdicts: frozenset[str] | set[str]
) -> list[str]:
    """Reasons this gate must not mint, one per artifact that fails the check.

    An artifact is untrusted when nothing produced it, when its producing node
    is not ``completed``, or when the most recent verdict recorded against it
    is one the contract calls a failure. Empty list means the gate may mint.
    """
    problems: list[str] = []
    for name, entry in sorted(prov.items()):
        producer = entry.get("produced_by") or ""
        status = entry.get("producer_status") or ""
        verdict = entry.get("last_verdict") or ""
        if not producer:
            problems.append(f"{name}: no step produced it")
            continue
        if status and status != "completed":
            problems.append(f"{name}: {producer} is {status}, not completed")
            continue
        if verdict in fail_verdicts:
            problems.append(
                f"{name}: last reviewed by {entry.get('written_by') or producer}"
                f" ({verdict})"
            )
    return problems


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
