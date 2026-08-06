"""
Session-driven workflow runs (research / --resume), in-process only.

A session is created, prompted (possibly many times across process restarts),
and closed. State persists in the RunStore (Redis or local file — see
run_store.py); a process's ``Sessions`` instance only caches in-memory copies
for the current run's lifetime.

No wire protocol here — callers are Python (the CLI's session_cli.py, or a
future thin stdio/editor adapter built on top of these functions).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Callable

import yaml

_USER_MARKER = re.compile(r"^\s*user\s*:\s*(?=\S)", re.MULTILINE | re.IGNORECASE)


class SessionError(Exception):
    """Raised for session-protocol failures. ``code`` keeps the old JSON-RPC
    numeric codes for test/log continuity; callers should match on type/message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnknownSessionError(SessionError):
    pass


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
) -> None:
    """Seed workflow state into ``state_path`` via shared ``seed_state_file``.

    ``worktree_path`` is left unset — artifact placement is entirely the
    workflow's decision (pack steps derive their own dir from CHANGE_ID /
    REPO_ROOT), not something the engine prescribes for session runs.
    """
    from orchestrator_next.run_loop import seed_state_file

    seed_state_file(
        state_path,
        slug=session_id,
        schema=schema,
        repo_root=repo_root,
        ticket_id=session_id,
    )


def _rematerialize_state(state_path: Path, content: str, repo_root: str) -> None:
    """Write a resumed session's state text, rebinding machine-specific paths.

    ``repo_root`` was stamped by whichever machine ran the session last; on
    resume it must point at this machine's path instead.
    """
    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        state_path.write_text(content, encoding="utf-8")
        return
    if isinstance(raw, dict):
        raw["repo_root"] = repo_root
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
    topic: str,
    session_id: str,
    repo_root: str,
    *,
    schema: str = "research",
    session_state: dict | None = None,
    on_update: Callable[[str], None] | None = None,
) -> dict:
    """Run or continue a session workflow via drive_loop.

    Durable source of truth is ``session_state['state_yaml_content']`` (Redis).
    Within a process, reuses ``_live_state_path`` so prompts do not rematerialize
    every turn. ``change_id`` / slug / ticket_id are the session_id.
    """
    from orchestrator_next.run_loop import LOOP_PAUSED, drive_loop, resolve_models_yaml

    def notify(text: str) -> None:
        if on_update is not None:
            on_update(text)

    if session_state is None:
        session_state = {}
    prompt = _extract_topic(topic) if topic.strip() else ""
    if prompt:
        parts = prompt.strip().split(maxsplit=1)
        known = set(_available_schemas(repo_root))
        if parts and parts[0].strip(" ,.:;").lower() in known:
            prompt = parts[1] if len(parts) > 1 else ""

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
            _rematerialize_state(Path(state_yaml_path), str(content), repo_root)
        else:
            _seed_session_state(session_id, schema, repo_root, Path(state_yaml_path))
            seeded_fresh = True
            notify(f"🔍 Workflow: {schema} (session {session_id})")
            if prompt:
                notify(f"  topic: {prompt}")
        session_state[_LIVE_STATE_KEY] = state_yaml_path

    keep_live = False
    try:
        if not seeded_fresh:
            if session_state.get("status") == "failed" and prompt:
                unlocked = _unlock_failed_for_retry(state_yaml_path)
                if unlocked:
                    notify(f"🔄 retrying failed step: {unlocked}")
            notify("➡️ continuing workflow" + (f": '{prompt}'" if prompt else ""))

        def on_event(kind: str, payload: dict) -> None:
            step_id = payload.get("step_id", "?")
            if kind == "await_input":
                ask = _ask_from_state(state_yaml_path)
                msg = f"⏸ {step_id} — input required"
                if ask:
                    msg += f"\nask: {ask}"
                notify(msg)
            elif kind == "step_start":
                if payload.get("kind") == "agent":
                    notify(f"→ {step_id} (agent, {payload.get('model')})")
                else:
                    notify(f"→ {step_id} (script)")
            elif kind == "step_done":
                if payload.get("kind") == "agent":
                    notify(
                        f"  ✓ {step_id} {payload.get('status', '?')} (rc={payload.get('rc')})",
                    )
                else:
                    ok = payload.get("ok", True)
                    notify(f"  ✓ {step_id} done" if ok else f"  ✗ {step_id} failed")
            elif kind == "complete":
                msg = (
                    "✅ workflow complete (state archived)"
                    if payload.get("archived")
                    else "✅ workflow complete"
                )
                notify(msg)
            elif kind == "blocked":
                notify("⛔ workflow blocked")
            elif kind == "error":
                notify(f"❌ {payload.get('message', 'workflow error')}")

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
    from orchestrator_next.run_store import REDIS_KEY_PREFIX

    return f"{REDIS_KEY_PREFIX}{session_id}"


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


def _delete_session_store(session_id: str) -> None:
    from orchestrator_next.run_store import open_store

    try:
        open_store().delete(session_id)
    except OSError:
        pass


def _session_load_result(session_id: str, session: dict) -> dict:
    """Payload for load_session — enough status for CLI resume decisions."""
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
    """Session ids in the RunStore (for post-restart discovery).

    Redis unreachable propagates — a listing that silently omitted persisted
    sessions would misreport "no sessions" instead of "store unavailable".
    """
    from orchestrator_next.run_store import open_store

    return open_store().list_ids()


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


def _ask_schema(session_id: str, repo_root: str | None = None, *, on_update=None) -> dict:
    """Return (and notify) a schema-selection question."""
    schemas = ", ".join(_available_schemas(repo_root))
    question = (
        "Which workflow should I run? "
        f"Available: {schemas}.\n"
        "Send the workflow name (e.g. \"research <topic>\") to continue."
    )
    if on_update is not None:
        on_update(question)
    return _completion("completed", question)


class Sessions:
    """In-memory session registry for one process, backed by the RunStore."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}

    def new_session(self, cwd: str | None = None, schema: str = "") -> str:
        session_id = str(uuid.uuid4())
        # Explicit arg or env pin wins; otherwise schema stays "" until the
        # first prompt declares one (or we ask).
        schema = str(
            schema or os.environ.get("ORCHESTRATOR_ACP_SCHEMA", "") or ""
        ).strip()
        self.sessions[session_id] = {
            "cwd": cwd or os.getcwd(),
            "schema": schema,
            "workflow": {"status": "active"},
        }
        try:
            _save_session(session_id, self.sessions[session_id])
        except RedisRequiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionError(-32011, f"failed to persist session: {exc}") from exc
        return session_id

    def load_session(self, session_id: str) -> dict:
        try:
            restored = _load_session(session_id) if session_id else None
        except OSError as exc:
            raise SessionError(-32010, f"session store unavailable: {exc}") from exc
        if restored is None:
            raise UnknownSessionError(-32002, f"unknown session: {session_id}")
        self.sessions[session_id] = restored
        return _session_load_result(session_id, restored)

    def prompt_session(
        self,
        session_id: str,
        text: str,
        *,
        on_update: Callable[[str], None] | None = None,
    ) -> dict:
        if session_id not in self.sessions:
            # Allow prompt after restart if the store has the session.
            try:
                restored = _load_session(session_id) if session_id else None
            except OSError as exc:
                raise SessionError(-32010, f"session store unavailable: {exc}") from exc
            if restored is None:
                raise UnknownSessionError(-32001, f"unknown session: {session_id}")
            self.sessions[session_id] = restored
        text = (text or "").strip()
        session = self.sessions[session_id]
        workflow = session.setdefault("workflow", {})
        has_state = bool(workflow.get("state_yaml_content"))
        if not text and not has_state:
            raise SessionError(-32602, "empty prompt")

        from orchestrator_next.run_store import open_store

        store = open_store()  # RedisRequiredError propagates to caller
        if not store.lock(session_id):
            raise SessionError(-32012, "session busy — another process is running it")
        try:
            repo_root = str(session.get("cwd") or os.getcwd())

            # Explicit declaration only (first word / "schema: X"); ask if missing.
            if not session.get("schema") and not has_state:
                route_text = _extract_topic(text)
                routed = _route_schema(route_text, repo_root=repo_root)
                if routed is None:
                    _save_session(session_id, session)
                    return _ask_schema(session_id, repo_root=repo_root, on_update=on_update)
                session["schema"] = routed
                if on_update is not None:
                    on_update(f"📋 routing to workflow: {routed}")

            result = run_workflow(
                text, session_id, repo_root,
                schema=str(session.get("schema") or "research"),
                session_state=workflow,
                on_update=on_update,
            )
            # Keep completed/failed/paused sessions in the store for --resume status.
            _save_session(session_id, session)
            return result
        except SessionError:
            raise
        except Exception as exc:  # noqa: BLE001
            workflow["status"] = "failed"
            try:
                _save_session(session_id, session)
            except Exception:  # noqa: BLE001
                pass
            raise SessionError(-32603, f"workflow error: {exc}") from exc
        finally:
            store.unlock(session_id)

    def close_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            _cleanup_session(self.sessions[session_id])
            del self.sessions[session_id]
        _delete_session_store(session_id)

    def list_sessions(self) -> list[str]:
        ids = set(self.sessions.keys())
        ids.update(_persisted_session_ids())
        return sorted(ids)
