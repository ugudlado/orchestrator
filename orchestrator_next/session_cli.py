"""CLI entry points for session-driven workflows (research / --resume).

Calls sessions.py directly — no protocol handshake needed in-process.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable

from orchestrator_next.sessions import SessionError, Sessions


def _is_uuid(text: str) -> bool:
    import uuid

    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


def _session_exists(session_id: str) -> bool:
    from orchestrator_next.run_store import open_store

    try:
        return open_store().load(session_id) is not None
    except Exception:  # noqa: BLE001
        return False


def _print_updates(text: str) -> None:
    print(text, flush=True)


def _outcome_text(result: dict) -> str:
    msgs = (result.get("outcome") or {}).get("messages") or []
    content = (msgs[0].get("content") or []) if msgs else []
    return str(content[0].get("text") or "") if content else ""


def _status_of(result: dict | None) -> str:
    if not result:
        return ""
    return str((result.get("outcome") or {}).get("outcome") or "")


def start_schema(
    schema: str,
    prompt: str,
    *,
    cwd: str | None = None,
    on_update: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """new_session + prompt_session. Returns session_id + outcome payload."""
    cwd = cwd or os.getcwd()
    notify = on_update or _print_updates
    sessions = Sessions()
    session_id = sessions.new_session(cwd=cwd, schema=schema)
    print(f"session_id={session_id}", flush=True)
    result = sessions.prompt_session(session_id, prompt, on_update=notify)
    text = _outcome_text(result)
    if text:
        print(text, flush=True)
    return {"session_id": session_id, "result": result, "status": _status_of(result)}


def resume_session(
    session_id: str,
    prompt: str = "",
    *,
    cwd: str | None = None,
    on_update: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """load_session + conditional prompt_session based on status."""
    notify = on_update or _print_updates
    sessions = Sessions()
    loaded = sessions.load_session(session_id)

    status = str(loaded.get("status") or "active").lower()
    ask = str(loaded.get("ask") or "")
    awaiting = loaded.get("awaiting_step_id")
    schema = loaded.get("schema") or "?"
    options = loaded.get("options") or []

    print(f"session_id={session_id}", flush=True)
    print(f"status={status} schema={schema}", flush=True)
    if awaiting:
        print(f"awaiting_step_id={awaiting}", flush=True)
    if ask:
        print(f"ask: {ask}", flush=True)
    for i, opt in enumerate(options, start=1):
        print(f"option_{i}: {(opt or {}).get('label') or ''}", flush=True)

    if status == "completed":
        print("session already completed — not re-running", flush=True)
        return {"session_id": session_id, "status": status, "loaded": loaded, "result": None}

    if status == "failed" and not (prompt or "").strip():
        print(
            "session failed — resume with direction to retry:\n"
            f'  orchestrator --resume {session_id} "try again with …"',
            flush=True,
        )
        return {"session_id": session_id, "status": status, "loaded": loaded, "result": None}

    result = sessions.prompt_session(session_id, prompt or "", on_update=notify)
    text = _outcome_text(result)
    if text:
        print(text, flush=True)
    return {
        "session_id": session_id,
        "status": _status_of(result) or status,
        "loaded": loaded,
        "result": result,
    }


def start_schema_main(schema: str, argv: list[str]) -> int:
    """CLI: ``orchestrator <schema> "<prompt>"``."""
    prompt = " ".join(argv).strip()
    if not prompt:
        print(f'usage: orchestrator {schema} "<prompt>"', file=sys.stderr)
        return 7
    if _is_uuid(prompt) and _session_exists(prompt):
        print(f"use: orchestrator --resume {prompt}", file=sys.stderr)
        return 7
    bare = schema.split("/", 1)[-1]
    try:
        out = start_schema(bare, prompt)
    except SessionError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 3
    return 3 if out.get("status") == "failed" else 0


def resume_main(session_id: str, user_input: str = "") -> int:
    """CLI: ``orchestrator --resume <session_id> ["optional input"]``."""
    if not session_id:
        print("usage: orchestrator --resume <session_id> [\"optional input\"]", file=sys.stderr)
        return 7
    try:
        out = resume_session(session_id, user_input)
    except SessionError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 3
    return 3 if out.get("status") == "failed" else 0


def is_session_schema(token: str) -> bool:
    """True when ``token`` is a session-driven schema (bare or pack/name)."""
    from orchestrator_next.paths import workflow_mode

    return workflow_mode(token) == "session"
