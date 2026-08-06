"""
ACP server mode for the orchestrator CLI (ORC-ACP).

`orchestrator acp` runs a JSON-RPC 2.0 server over stdio speaking the Agent
Client Protocol (ACP). A client (Hermes, an editor, another agent) can:

  initialize     → protocol handshake
  session/new    → create a workflow session
  session/prompt → run a workflow step / full workflow, streaming
                   session/update notifications (agent_message_chunk) as
                   steps progress, then return a completion result

Wire format (matches agentclientprotocol.org + Hermes' own ACP client):
  Request:  {"jsonrpc":"2.0","id":N,"method":"...","params":{...}}\n
  Response: {"jsonrpc":"2.0","id":N,"result":{...}}\n
  Notify:   {"jsonrpc":"2.0","method":"session/update",
             "params":{"update":{"sessionUpdate":"agent_message_chunk",
                                  "content":{"type":"text","text":"..."}}}}\n

Everything on stdout is a valid ACP message; diagnostics go to stderr.

Workflows run through the real engine: seed state, dispatch, run each step,
stream session/update notifications as steps progress, return a completion.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

import yaml

# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

# Last user-role marker at a line boundary (inline "User: x", block
# "User:\nx", any case). The workflow topic is the last user turn.
_USER_MARKER = re.compile(r"^\s*user\s*:\s*(?=\S)", re.MULTILINE | re.IGNORECASE)

# Optional in-process sink (acp_client); None → stdout (Hermes / `orchestrator acp`).
_send_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("acp_send_sink", default=None)

def _send(obj: dict) -> None:
    """Write one JSON-RPC message to the active sink (stdout or in-process)."""
    sink = _send_sink.get()
    if sink is not None:
        sink(obj)
        return
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(message_id: int | None, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "result": result})


def _error(message_id: int | None, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def _notify(session_id: str, text: str, kind: str = "agent_message_chunk") -> None:
    """Stream a session/update notification to the client."""
    _send({
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": kind,
                "content": {"type": "text", "text": text},
            }
        },
    })


class RedisRequiredError(RuntimeError):
    """Raised when a session workflow needs Redis but none is configured."""


def redis_url() -> str:
    return (
        os.environ.get("ORCHESTRATOR_ACP_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or ""
    ).strip()


def require_redis():
    """Return a Redis client or raise RedisRequiredError with a clear message."""
    client = _redis_client()
    if client is None:
        raise RedisRequiredError(
            "Redis is required for session workflows (research / --resume). "
            "Set REDIS_URL or ORCHESTRATOR_ACP_REDIS_URL."
        )
    return client


def reset_redis_client_cache() -> None:
    """Clear the lazy Redis singleton (tests / URL changes)."""
    global _redis, _redis_checked
    _redis = None
    _redis_checked = False


def session_workspace(repo_root: str, session_id: str) -> Path:
    """Artifact workspace for a session run (no durable *_state.yaml here)."""
    return Path(repo_root) / ".orchestrator" / "sessions" / session_id

# ---------------------------------------------------------------------------
# Workflow driver (real engine via shared drive_loop)
# ---------------------------------------------------------------------------

def _final_state_text(state_yaml_path: str) -> str:
    """Human summary of a completed run: step history + artifact pointers."""
    from orchestrator_next.report import load_state as load_state_raw

    raw = load_state_raw(state_yaml_path)
    if not raw:
        return "workflow finished"
    steps = [
        f"- {entry.get('step_id')}: {entry.get('status')}"
        for entry in (raw.get("step_history") or [])
        if isinstance(entry, dict) and entry.get("step_id")
    ]
    parts = [f"workflow '{raw.get('schema', '?')}' completed"]
    if steps:
        parts.append("steps:\n" + "\n".join(steps))
    return "\n".join(parts)


def _ask_from_state(state_yaml_path: str) -> str:
    """Pull outputs.ask from the latest step_history entry, if any."""
    from orchestrator_next.report import load_state as load_state_raw

    raw = load_state_raw(state_yaml_path) or {}
    hist = raw.get("step_history") or []
    if not hist or not isinstance(hist[-1], dict):
        return ""
    ask = (hist[-1].get("outputs") or {}).get("ask")
    return str(ask).strip() if ask else ""


def _extract_topic(prompt_text: str) -> str:
    """Last user turn from a Hermes-style ACP transcript (not the system preamble)."""
    text = prompt_text.strip()
    matches = list(_USER_MARKER.finditer(text))
    if matches:
        text = text[matches[-1].end():].strip()
    for cut in ("\nContinue the conversation", "\nAvailable tools"):
        pos = text.find(cut)
        if pos != -1:
            text = text[:pos].strip()
            break
    return text or "research"


def _seed_session_state(
    session_id: str,
    schema: str,
    repo_root: str,
    state_path: Path,
    *,
    workspace: Path,
) -> None:
    """Seed workflow state into ``state_path`` via shared ``seed_state_file``."""
    from orchestrator_next.run_loop import seed_state_file

    workspace.mkdir(parents=True, exist_ok=True)
    seed_state_file(
        state_path,
        slug=session_id,
        schema=schema,
        repo_root=repo_root,
        worktree_path=str(workspace),
        # Session identity: keep ticket_id=session_id for step env / CHANGE_ID compat.
        ticket_id=session_id,
    )


def _rematerialize_state(state_path: Path, content: str, repo_root: str, workspace: Path) -> None:
    """Write a resumed session's state text, rebinding machine-specific paths.

    ``repo_root`` / ``worktree_path`` were stamped by whichever machine ran the
    session last; on resume they must point at this machine's paths instead.
    """
    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        state_path.write_text(content, encoding="utf-8")
        return
    if isinstance(raw, dict):
        raw["repo_root"] = repo_root
        raw["worktree_path"] = str(workspace)
        content = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    state_path.write_text(content, encoding="utf-8")


_LIVE_STATE_KEY = "_live_state_path"  # in-process only; never persisted to Redis


def _unlock_failed_for_retry(state_path: str) -> str | None:
    """Reset a failed/blocked node so drive_loop can re-run it. Returns step_id if unlocked."""
    from orchestrator_next.reset_step import apply_dag_reset

    path = Path(state_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    phase = str(raw.get("phase") or "main")
    failed_step: str | None = None
    for entry in reversed(raw.get("step_history") or []):
        if isinstance(entry, dict) and entry.get("status") == "failed":
            failed_step = str(entry.get("step_id") or "")
            phase = str(entry.get("phase") or phase)
            break
    if not failed_step:
        nxt = raw.get("next_step") if isinstance(raw.get("next_step"), dict) else {}
        failed_step = str(nxt.get("step_id") or "") or None
    if not failed_step:
        return None
    try:
        apply_dag_reset(raw, phase, failed_step, keep_history_for=failed_step)
    except ValueError:
        return None
    raw["status"] = "active"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return failed_step


def _snapshot_state_to_session(session_state: dict, state_yaml_path: str) -> None:
    """Copy temp state.yaml into Redis-backed session fields; drop durable path."""
    path = Path(state_yaml_path)
    if path.is_file():
        session_state["state_yaml_content"] = path.read_text(encoding="utf-8")
    session_state.pop("state_yaml_path", None)


def _discard_temp_state(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def run_workflow(
    topic: str, session_id: str, repo_root: str,
    *,
    schema: str = "research",
    session_state: dict | None = None,
) -> dict:
    """Run or continue a session workflow via drive_loop.

    Durable source of truth is ``session_state['state_yaml_content']`` (Redis).
    Within a process, reuses ``_live_state_path`` so prompts do not rematerialize
    every turn. ``change_id`` / slug / ticket_id are the session_id.
    """
    from orchestrator_next.run_loop import LOOP_PAUSED, drive_loop, resolve_models_yaml

    if session_state is None:
        session_state = {}
    prompt = _extract_topic(topic) if topic.strip() else ""
    # Strip a leading schema token when the agent declared it in the prompt text.
    if prompt:
        parts = prompt.strip().split(maxsplit=1)
        known = set(_available_schemas(repo_root))
        if parts and parts[0].strip(" ,.:;").lower() in known:
            prompt = parts[1] if len(parts) > 1 else ""

    workspace = session_workspace(repo_root, session_id)
    workspace.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="orc-acp-"))

    live = session_state.get(_LIVE_STATE_KEY)
    reuse_live = bool(live and Path(str(live)).is_file())
    seeded_fresh = False
    if reuse_live:
        state_yaml_path = str(live)
    else:
        fd, state_yaml_path = tempfile.mkstemp(
            prefix=f"orc-sess-{session_id[:8]}-", suffix="_state.yaml",
        )
        os.close(fd)
        content = session_state.get("state_yaml_content")
        if content:
            _rematerialize_state(Path(state_yaml_path), str(content), repo_root, workspace)
        else:
            _seed_session_state(
                session_id, schema, repo_root, Path(state_yaml_path), workspace=workspace,
            )
            seeded_fresh = True
            _notify(session_id, f"🔍 Workflow: {schema} (session {session_id})")
            if prompt:
                _notify(session_id, f"  topic: {prompt}")
        session_state[_LIVE_STATE_KEY] = state_yaml_path

    keep_live = False
    try:
        if not seeded_fresh:
            if session_state.get("status") == "failed" and prompt:
                unlocked = _unlock_failed_for_retry(state_yaml_path)
                if unlocked:
                    _notify(session_id, f"🔄 retrying failed step: {unlocked}")
            _notify(
                session_id,
                "➡️ continuing workflow" + (f": '{prompt}'" if prompt else ""),
            )

        def on_event(kind: str, payload: dict) -> None:
            step_id = payload.get("step_id", "?")
            if kind == "await_input":
                ask = _ask_from_state(state_yaml_path)
                msg = f"⏸ {step_id} — input required"
                if ask:
                    msg += f"\nask: {ask}"
                _notify(session_id, msg)
            elif kind == "step_start":
                if payload.get("kind") == "agent":
                    _notify(session_id, f"→ {step_id} (agent, {payload.get('model')})")
                else:
                    _notify(session_id, f"→ {step_id} (script)")
            elif kind == "step_done":
                if payload.get("kind") == "agent":
                    _notify(
                        session_id,
                        f"  ✓ {step_id} {payload.get('status', '?')} (rc={payload.get('rc')})",
                    )
                else:
                    ok = payload.get("ok", True)
                    _notify(session_id, f"  ✓ {step_id} done" if ok else f"  ✗ {step_id} failed")
            elif kind == "complete":
                msg = (
                    "✅ workflow complete (state archived)"
                    if payload.get("archived")
                    else "✅ workflow complete"
                )
                _notify(session_id, msg)
            elif kind == "blocked":
                _notify(session_id, "⛔ workflow blocked")
            elif kind == "error":
                _notify(session_id, f"❌ {payload.get('message', 'workflow error')}")

        result = drive_loop(
            state_yaml_path,
            repo_root=repo_root,
            models_yaml=resolve_models_yaml(repo_root=repo_root),
            tmp_dir=tmp_dir,
            keep_tmp=False,
            user_direction=prompt,
            pause_on_await_input=True,
            on_event=on_event,
        )
        state_yaml_path = result.state_yaml_path
        session_state[_LIVE_STATE_KEY] = state_yaml_path
        _snapshot_state_to_session(session_state, state_yaml_path)
        ask = _ask_from_state(state_yaml_path)

        if result.code == LOOP_PAUSED:
            session_state["status"] = "await_input"
            session_state["awaiting_step_id"] = result.awaiting_step_id
            session_state["ask"] = ask
            keep_live = True
            return _completion("await_input", ask or _final_state_text(state_yaml_path))

        session_state.pop("awaiting_step_id", None)
        session_state.pop("ask", None)

        if result.code == 1:
            session_state["status"] = "completed"
            return _completion("completed", _final_state_text(state_yaml_path))

        if result.code == 3:
            session_state["status"] = "failed"
            keep_live = True  # allow --resume retry without rematerialize
            return _completion("failed", _final_state_text(state_yaml_path))

        if result.code == 2:
            session_state["status"] = "blocked"
            keep_live = True
            return _completion("cancelled", _final_state_text(state_yaml_path))

        session_state["status"] = "active"
        keep_live = True
        return _completion("completed", _final_state_text(state_yaml_path))
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if not keep_live:
            live_path = session_state.pop(_LIVE_STATE_KEY, None)
            _discard_temp_state(live_path if live_path else state_yaml_path)


def _completion(outcome: str, text: str) -> dict:
    return {
        "outcome": {
            "outcome": outcome,
            "messages": [
                {"role": "assistant", "content": [{"type": "text", "text": text}]}
            ],
        }
    }


def _cleanup_session(session: dict) -> None:
    """Tear down per-session workflow resources (tmp dirs, leftover state)."""
    workflow = session.get("workflow") or {}
    tmp_dir = workflow.pop("tmp_dir", None)
    if tmp_dir:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    _discard_temp_state(workflow.pop(_LIVE_STATE_KEY, None))
    workflow.pop("state_yaml_path", None)
    workflow.pop("awaiting_step_id", None)
    # Artifact workspace is removed by session/close via _delete_session_artifacts.


# ---------------------------------------------------------------------------
# Session persistence (RunStore — Redis or local file, cross-process continuation)
# ---------------------------------------------------------------------------

_redis = None
_redis_checked = False


def _redis_client():
    """Lazy Redis singleton if configured; None otherwise."""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    url = redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore
        _redis = redis.from_url(url, decode_responses=True)
    except ImportError:
        _redis = None
    return _redis


def _session_redis_key(session_id: str) -> str:
    return f"orc:acp:session:{session_id}"


def _save_session(session_id: str, session: dict) -> None:
    """Persist session meta + state_yaml_content snapshot to the RunStore."""
    from orchestrator_next.run_store import open_store

    workflow = dict(session.get("workflow") or {})
    state_yaml_path = workflow.get("state_yaml_path") or workflow.get(_LIVE_STATE_KEY)
    if state_yaml_path and Path(str(state_yaml_path)).is_file():
        try:
            workflow["state_yaml_content"] = Path(str(state_yaml_path)).read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(f"failed reading temp state for session {session_id}: {exc}") from exc
    # Never persist process-local paths for session runs.
    workflow.pop("state_yaml_path", None)
    workflow.pop(_LIVE_STATE_KEY, None)
    payload = {
        "cwd": session.get("cwd"),
        "schema": session.get("schema", "research"),
        "workflow": workflow,
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    open_store().save(session_id, encoded)


def _load_session(session_id: str) -> dict | None:
    """Restore a persisted session from the RunStore. No rematerialize."""
    from orchestrator_next.run_store import open_store

    try:
        raw = open_store().load(session_id)
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        workflow = dict(data.get("workflow") or {})
        workflow.pop("state_yaml_path", None)
        workflow.pop(_LIVE_STATE_KEY, None)
        # Stored cwd is a hint — rebind to this machine if it doesn't exist here.
        cwd = str(data.get("cwd") or "")
        if not cwd or not Path(cwd).is_dir():
            cwd = os.getcwd()
        return {
            "cwd": cwd,
            "schema": str(data.get("schema") or "research").strip(),
            "workflow": workflow,
        }
    except (json.JSONDecodeError, TypeError):
        return None


def _delete_session_artifacts(repo_root: str, session_id: str) -> None:
    import shutil
    workspace = session_workspace(repo_root, session_id)
    if workspace.is_dir():
        shutil.rmtree(workspace, ignore_errors=True)


def _delete_session_store(session_id: str) -> None:
    from orchestrator_next.run_store import open_store

    try:
        open_store().delete(session_id)
    except OSError:
        pass


def _session_load_result(session_id: str, session: dict) -> dict:
    """Payload for session/load — enough status for CLI resume decisions."""
    workflow = session.get("workflow") or {}
    return {
        "sessionId": session_id,
        "cwd": session.get("cwd"),
        "schema": session.get("schema", "research"),
        "status": workflow.get("status") or "active",
        "awaiting_step_id": workflow.get("awaiting_step_id"),
        "ask": workflow.get("ask") or "",
        "has_state": bool(workflow.get("state_yaml_content")),
    }


def _persisted_session_ids() -> list[str]:
    """Session ids in the RunStore (for post-restart discovery)."""
    from orchestrator_next.run_store import open_store

    try:
        return open_store().list_ids()
    except (OSError, RedisRequiredError):
        return []


def _available_schemas(repo_root: str | None = None) -> list[str]:
    """Installed workflow schema names."""
    try:
        from orchestrator_next.paths import list_workflows
        root = Path(repo_root) if repo_root else None
        names = sorted(list_workflows(root).keys())
        if names:
            return names
    except Exception:  # noqa: BLE001
        pass
    return ["research"]


def _route_schema(text: str, repo_root: str | None = None) -> str | None:
    """Return an explicitly declared installed schema name, else None.

    Synonyms are intentionally NOT matched — undeclared requests ask the agent.
    Uses ``_available_schemas`` only (no parallel hard-coded name list).
    """
    known = {n.lower() for n in _available_schemas(repo_root)}
    if not known:
        return None
    low = (text or "").strip().lower()
    if not low:
        return None
    first_word = low.split(maxsplit=1)[0].strip(" ,.:;")
    if first_word in known:
        return first_word
    for prefix in ("schema:", "workflow:"):
        if low.startswith(prefix):
            rest = low[len(prefix):].strip()
            word = rest.split(maxsplit=1)[0].strip(" ,.:;\"'") if rest else ""
            if word in known:
                return word
    return None


def _ask_schema(session_id: str, repo_root: str | None = None) -> dict:
    """Stream a schema-selection question back to the client."""
    schemas = ", ".join(_available_schemas(repo_root))
    question = (
        "Which workflow should I run? "
        f"Available: {schemas}.\n"
        "Send the workflow name (e.g. \"research <topic>\") to continue."
    )
    _notify(session_id, question)
    return _completion("completed", question)


# ---------------------------------------------------------------------------
# Session + method dispatch
# ---------------------------------------------------------------------------

class AcpServer:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}

    def invoke(
        self,
        method: str,
        params: dict | None = None,
        *,
        on_update: Callable[[str], None] | None = None,
    ) -> dict:
        """In-process JSON-RPC call. Returns result dict; raises AcpRpcError on error."""
        box: dict[str, Any] = {}

        def sink(obj: dict) -> None:
            if obj.get("method") == "session/update":
                update = (obj.get("params") or {}).get("update") or {}
                content = update.get("content") or {}
                text = content.get("text", "") if isinstance(content, dict) else ""
                if text and on_update is not None:
                    on_update(text)
                return
            if "error" in obj:
                box["error"] = obj["error"]
            elif "result" in obj:
                box["result"] = obj["result"]

        token = _send_sink.set(sink)
        try:
            self.handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or {},
            })
        finally:
            _send_sink.reset(token)
        if "error" in box:
            err = box["error"] or {}
            raise AcpRpcError(err.get("code", -32000), str(err.get("message", "error")))
        return box.get("result") or {}

    def handle(self, msg: dict) -> None:
        method = msg.get("method")
        message_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            _result(message_id, {
                "protocolVersion": 1,
                "capabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    # Agent declares which schema to run; server lists what's installed.
                    "workflows": {"schemas": _available_schemas()},
                },
                "agentCapabilities": {},
                "serverInfo": {"name": "orchestrator", "version": "0.1.0"},
            })
            return

        if method == "session/schemas":
            _result(message_id, {"schemas": _available_schemas()})
            return

        if method == "session/new":
            session_id = str(uuid.uuid4())
            # Explicit env pin or client hint wins; otherwise schema stays ""
            # until the first prompt declares one (or we ask).
            schema = str(
                params.get("schema")
                or os.environ.get("ORCHESTRATOR_ACP_SCHEMA", "")
                or ""
            ).strip()
            self.sessions[session_id] = {
                "cwd": params.get("cwd") or os.getcwd(),
                "schema": schema,  # "" = unset → route from first prompt
                "workflow": {"status": "active"},
            }
            try:
                _save_session(session_id, self.sessions[session_id])
            except Exception as exc:  # noqa: BLE001
                _error(message_id, -32011, f"failed to persist session: {exc}")
                return
            _result(message_id, {"sessionId": session_id})
            return

        if method == "session/load":
            session_id = params.get("sessionId")
            try:
                restored = _load_session(session_id) if session_id else None
            except OSError as exc:
                _error(message_id, -32010, f"session store unavailable: {exc}")
                return
            if restored is None:
                _error(message_id, -32002, f"unknown session: {session_id}")
                return
            self.sessions[session_id] = restored
            _result(message_id, _session_load_result(session_id, restored))
            return

        if method == "session/prompt":
            session_id = params.get("sessionId")
            if session_id not in self.sessions:
                # Allow prompt after restart if the store has the session.
                try:
                    restored = _load_session(session_id) if session_id else None
                except OSError as exc:
                    _error(message_id, -32010, f"session store unavailable: {exc}")
                    return
                if restored is None:
                    _error(message_id, -32001, f"unknown session: {session_id}")
                    return
                self.sessions[session_id] = restored
            prompt = params.get("prompt") or []
            if isinstance(prompt, str):
                text = prompt.strip()
            else:
                text = " ".join(
                    str(p.get("text", "")) for p in prompt if isinstance(p, dict)
                ).strip()
            session = self.sessions[session_id]
            workflow = session.setdefault("workflow", {})
            has_state = bool(workflow.get("state_yaml_content"))
            if not text and not has_state:
                _error(message_id, -32602, "empty prompt")
                return

            from orchestrator_next.run_store import open_store

            try:
                store = open_store()
            except RedisRequiredError as exc:
                _error(message_id, -32010, str(exc))
                return
            if not store.lock(session_id):
                _error(message_id, -32012, "session busy — another process is running it")
                return
            try:
                repo_root = str(session.get("cwd") or os.getcwd())

                # Explicit declaration only (first word / "schema: X"); ask if missing.
                if not session.get("schema") and not has_state:
                    route_text = _extract_topic(text)
                    routed = _route_schema(route_text, repo_root=repo_root)
                    if routed is None:
                        _save_session(session_id, session)
                        _result(message_id, _ask_schema(session_id, repo_root=repo_root))
                        return
                    session["schema"] = routed
                    _notify(session_id, f"📋 routing to workflow: {routed}")

                result = run_workflow(
                    text, session_id, repo_root,
                    schema=str(session.get("schema") or "research"),
                    session_state=workflow,
                )
                # Keep completed/failed/paused sessions in the store for --resume status.
                _save_session(session_id, session)
                _result(message_id, result)
            except Exception as exc:  # noqa: BLE001
                workflow["status"] = "failed"
                try:
                    _save_session(session_id, session)
                except Exception:  # noqa: BLE001
                    pass
                _error(message_id, -32603, f"workflow error: {exc}")
            finally:
                store.unlock(session_id)
            return

        if method == "session/close":
            session_id = params.get("sessionId")
            repo_root = None
            if session_id in self.sessions:
                sess = self.sessions[session_id]
                repo_root = str(sess.get("cwd") or os.getcwd())
                _cleanup_session(sess)
                _delete_session_artifacts(repo_root, session_id)
                del self.sessions[session_id]
            _delete_session_store(session_id)
            _result(message_id, {})
            return

        if method == "session/list":
            # In-memory sessions union persisted store ids (restart discovery).
            ids = set(self.sessions.keys())
            ids.update(_persisted_session_ids())
            _result(message_id, {"sessionIds": sorted(ids)})
            return

        _error(message_id, -32601, f"method not found: {method}")


class AcpRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def main() -> int:
    """Stdio ACP loop with a reader thread so stdin stays drained during long prompts."""
    import queue
    import threading

    server = AcpServer()
    lines: queue.Queue[str | None] = queue.Queue()

    def _stdin_reader() -> None:
        try:
            for line in sys.stdin:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=_stdin_reader, name="acp-stdin", daemon=True).start()
    while True:
        line = lines.get()
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error")
            continue
        try:
            server.handle(msg)
        except Exception as exc:  # noqa: BLE001
            _error(msg.get("id"), -32603, f"handler error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
