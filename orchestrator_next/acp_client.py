"""In-process ACP client — same handlers Hermes uses over stdio.

Human CLI (`orchestrator research` / `--resume`) calls ``AcpServer.invoke``
directly. Redis holds session state.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable

from orchestrator_next.acp_server import (
    AcpRpcError,
    AcpServer,
    RedisRequiredError,
)

_CLIENT_INFO = {"name": "orchestrator-cli", "version": "0.1"}


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


def _new_server() -> AcpServer:
    server = AcpServer()
    server.invoke(
        "initialize",
        {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": _CLIENT_INFO,
        },
    )
    return server


def start_schema(
    schema: str,
    prompt: str,
    *,
    cwd: str | None = None,
    on_update: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """session/new + session/prompt. Returns session_id + outcome payload."""
    cwd = cwd or os.getcwd()
    notify = on_update or _print_updates
    server = _new_server()
    created = server.invoke(
        "session/new",
        {"cwd": cwd, "schema": schema},
        on_update=notify,
    )
    session_id = str(created.get("sessionId") or "")
    if not session_id:
        raise AcpRpcError(-32000, "session/new returned no sessionId")
    print(f"session_id={session_id}", flush=True)
    result = server.invoke(
        "session/prompt",
        {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": prompt}],
        },
        on_update=notify,
    )
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
    """session/load + conditional session/prompt based on status."""
    notify = on_update or _print_updates
    server = _new_server()
    loaded = server.invoke(
        "session/load",
        {"sessionId": session_id},
        on_update=notify,
    )

    status = str(loaded.get("status") or "active").lower()
    ask = str(loaded.get("ask") or "")
    awaiting = loaded.get("awaiting_step_id")
    schema = loaded.get("schema") or "?"

    print(f"session_id={session_id}", flush=True)
    print(f"status={status} schema={schema}", flush=True)
    if awaiting:
        print(f"awaiting_step_id={awaiting}", flush=True)
    if ask:
        print(f"ask: {ask}", flush=True)

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

    result = server.invoke(
        "session/prompt",
        {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": prompt or ""}],
        },
        on_update=notify,
    )
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
    except RedisRequiredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 7
    except AcpRpcError as exc:
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
    except RedisRequiredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 7
    except AcpRpcError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 3
    return 3 if out.get("status") == "failed" else 0


def is_session_schema(token: str) -> bool:
    """True when ``token`` is a session-driven schema (bare or pack/name)."""
    from orchestrator_next.paths import workflow_mode

    return workflow_mode(token) == "session"
