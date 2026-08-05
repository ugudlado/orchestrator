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

The research workflow (config/workflows/research.yaml) runs through the real
engine: seed state, dispatch, run each step, stream session/update
notifications as steps progress, return a completion result.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

# Last user-role marker at a line boundary (inline "User: x", block
# "User:\nx", any case). The workflow topic is the last user turn.
_USER_MARKER = re.compile(r"^\s*user\s*:\s*(?=\S)", re.MULTILINE | re.IGNORECASE)

def _send(obj: dict) -> None:
    """Write one JSON-RPC message to stdout (the ACP channel)."""
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
            "update": {
                "sessionUpdate": kind,
                "content": {"type": "text", "text": text},
            }
        },
    })


# ---------------------------------------------------------------------------
# Research workflow driver (real engine)
# ---------------------------------------------------------------------------

def _final_state_text(state_yaml_path: str) -> str:
    """Human summary of a completed run: step history + artifact pointers."""
    try:
        import yaml
        raw = yaml.safe_load(Path(state_yaml_path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return "workflow finished"
    steps = []
    for entry in raw.get("step_history") or []:
        sid = entry.get("step_id") if isinstance(entry, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        if sid:
            steps.append(f"- {sid}: {status}")
    parts = [f"workflow '{raw.get('schema', '?')}' completed"]
    if steps:
        parts.append("steps:\n" + "\n".join(steps))
    return "\n".join(parts)


def _extract_topic(prompt_text: str) -> str:
    """Pull the actual user request out of the formatted ACP prompt.

    Hermes sends the whole conversation (system + transcript + user) as one
    prompt text. The workflow topic is the last User: section, not the system
    preamble or model hints.

    Matches the user-role marker at a line boundary in any of the common
    formats — ``User: text`` (inline), ``User:\\ntext`` (block), lowercase
    ``user:``, and capitalised variants — then returns everything after the
    LAST match (the most recent user turn).
    """
    text = prompt_text.strip()
    # Take everything after the LAST user-role marker (most recent turn).
    matches = list(_USER_MARKER.finditer(text))
    if matches:
        text = text[matches[-1].end():].strip()
    # Drop trailing instructions the client appends after the transcript.
    for cut in ("\nContinue the conversation", "\nAvailable tools"):
        pos = text.find(cut)
        if pos != -1:
            text = text[:pos].strip()
            break
    return text or "research"


def run_workflow(
    topic: str, session_id: str, repo_root: str,
    *,
    schema: str = "research",
    session_state: dict | None = None,
) -> dict:
    """Run or continue a workflow through the real engine (multi-turn aware).

    Generic driver — the workflow config decides where input is needed:
    steps whose contract declares ``await_input: true`` pause the run; the
    next prompt's text is injected as "User direction" and the step runs.
    Steps without await_input run automatically (fire-and-forget), so a
    workflow designed for one-shot use keeps working unchanged.

    The caller keeps ``session_state`` (per-session dict) across prompts.
    """
    from orchestrator_next.run_loop import (
        run_agent_step,
        run_script_step,
        _seed_state,
    )
    from orchestrator_next.parser import load_state
    from orchestrator_next.record import record
    from orchestrator_next.paths import config_root

    if session_state is None:
        session_state = {}
    state_yaml_path = session_state.get("state_yaml_path")
    prompt = _extract_topic(topic)
    tmp_dir = Path(session_state.get("tmp_dir") or tempfile.mkdtemp(prefix="orc-acp-"))
    session_state["tmp_dir"] = str(tmp_dir)

    def _new_workflow() -> str:
        slug_src = prompt
        # Strip a leading schema keyword so the slug is clean
        # ("research postgres" → slug "postgres", not "research-postgres").
        if slug_src:
            first = slug_src.strip().split(maxsplit=1)[0].strip(" ,.:;").lower()
            if first in _SCHEMA_HINTS:
                rest = slug_src.strip().split(maxsplit=1)[1:] 
                slug_src = rest[0] if rest else ""
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug_src.lower()).strip("-")
        if not slug:
            slug = schema
        slug = slug[:80].rstrip("-")  # keep state filenames sane
        return _seed_state(slug, schema, repo_root)

    if not state_yaml_path:
        state_yaml_path = _new_workflow()
        session_state["state_yaml_path"] = state_yaml_path
        _notify(session_id, f"🔍 Workflow: {schema} on '{prompt}'")
        _notify(session_id, f"  state: {state_yaml_path}")
    else:
        # Continuation — the new prompt text guides the awaited step.
        _notify(session_id, f"➡️ continuing workflow: '{prompt}'")

    try:
        state_yaml = state_yaml_path
        while True:
            if not Path(state_yaml).is_file():
                _notify(session_id, "✅ workflow complete (state archived)")
                session_state.pop("state_yaml_path", None)
                session_state.pop("tmp_dir", None)
                session_state.pop("awaiting_step_id", None)
                break
            state = load_state(state_yaml)
            from orchestrator_next.dispatch import dispatch

            try:
                action, code = dispatch(state, state_yaml)
            except Exception as exc:  # noqa: BLE001
                _error_to_notify(session_id, f"dispatch error: {exc}")
                return _completion("completed", f"workflow error: {exc}")

            if code == 1:
                _notify(session_id, "✅ workflow complete")
                session_state.pop("state_yaml_path", None)
                session_state.pop("tmp_dir", None)
                session_state.pop("awaiting_step_id", None)
                break
            if code == 2:
                _notify(session_id, "⛔ workflow blocked")
                break
            if code == 3:
                _notify(session_id, "❌ workflow error")
                break

            step_id = action.get("step_id", "?")
            needs_input = bool(action.get("await_input"))

            # Config-declared input gate: pause until the client sends the
            # next prompt (which becomes the User direction for this step).
            if needs_input and session_state.get("awaiting_step_id") != step_id:
                session_state["awaiting_step_id"] = step_id
                _notify(session_id, f"⏸ {step_id} — input required; send direction to continue")
                break

            if action.get("model"):
                _notify(session_id, f"→ {step_id} (agent, {action.get('model')})")
                # Inject the user's continuation text as guidance for the agent.
                if prompt:
                    base = action.get("instruction") or ""
                    action["instruction"] = (
                        f"{base}\n\nUser direction: {prompt}"
                        if base
                        else f"User direction: {prompt}"
                    )
                payload = run_agent_step(
                    action,
                    repo_root=repo_root,
                    models_yaml=str(config_root() / "models.yaml"),
                    state_raw=state.raw,
                    state_yaml_path=state_yaml,
                    tmp_dir=tmp_dir,
                )
                result, rc = record(state_yaml, payload)
                _notify(session_id, f"  ✓ {step_id} {payload.get('status', '?')} (rc={rc})")
                if needs_input:
                    session_state.pop("awaiting_step_id", None)
            elif action.get("run"):
                _notify(session_id, f"→ {step_id} (script)")
                ok, state_yaml = run_script_step(action, state_yaml_path=state_yaml, state=state)
                _notify(session_id, f"  ✓ {step_id} done" if ok else f"  ✗ {step_id} failed")
                if not ok:
                    break
                if needs_input:
                    session_state.pop("awaiting_step_id", None)
            else:
                _notify(session_id, f"→ {step_id} (no action)")
                break
    finally:
        pass  # tmp_dir cleaned when session ends via close()

    return _completion("completed", _final_state_text(state_yaml_path))


def _error_to_notify(session_id: str, text: str) -> None:
    _notify(session_id, text, kind="agent_thought_chunk")


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
    workflow.pop("state_yaml_path", None)
    workflow.pop("awaiting_step_id", None)


# ---------------------------------------------------------------------------
# Session persistence (cross-process + cross-environment continuation)
# ---------------------------------------------------------------------------

def _session_store_dir() -> Path:
    """Directory holding persisted ACP sessions (survives server restarts)."""
    root = os.environ.get("ORCHESTRATOR_ACP_SESSION_DIR") or str(
        Path.home() / ".orchestrator" / "acp-sessions"
    )
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_store_path(session_id: str) -> Path:
    return _session_store_dir() / f"{session_id}.json"


def _redis_client():
    """Lazy Redis client if configured; None otherwise."""
    url = os.environ.get("ORCHESTRATOR_ACP_REDIS_URL") or os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis  # type: ignore
        return redis.from_url(url, decode_responses=True)
    except ImportError:
        return None


def _session_redis_key(session_id: str) -> str:
    return f"orc:acp:session:{session_id}"


def _save_session(session_id: str, session: dict) -> None:
    """Persist a session's resumable state (best-effort).

    Carries the state.yaml CONTENT (not just its path) so a session can be
    resumed from a different machine/cloud environment: the store is the
    source of truth for 'wherever we left off'.
    """
    try:
        workflow = dict(session.get("workflow") or {})
        state_yaml_path = workflow.get("state_yaml_path")
        if state_yaml_path and Path(state_yaml_path).is_file():
            try:
                workflow["state_yaml_content"] = Path(state_yaml_path).read_text(
                    encoding="utf-8"
                )
            except OSError:
                workflow.pop("state_yaml_content", None)
        payload = {
            "cwd": session.get("cwd"),
            "schema": session.get("schema", "research"),
            "mcpServers": session.get("mcpServers") or [],
            "workflow": workflow,
        }
        encoded = json.dumps(payload, indent=2)
        client = _redis_client()
        if client is not None:
            client.set(_session_redis_key(session_id), encoded)
        else:
            _session_store_path(session_id).write_text(encoded, encoding="utf-8")
    except OSError:
        pass  # persistence is best-effort


def _load_session(session_id: str) -> dict | None:
    """Restore a persisted session, or None if unknown.

    If the stored workflow references a state.yaml that does not exist on
    this machine but its content was persisted, materialize it so the engine
    can dispatch the next step from exactly where the workflow left off.
    """
    try:
        client = _redis_client()
        if client is not None:
            raw = client.get(_session_redis_key(session_id))
        else:
            path = _session_store_path(session_id)
            if not path.is_file():
                return None
            raw = path.read_text(encoding="utf-8")
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        workflow = dict(data.get("workflow") or {})
        state_yaml_path = workflow.get("state_yaml_path")
        state_yaml_content = workflow.get("state_yaml_content")
        if state_yaml_path and state_yaml_content and not Path(state_yaml_path).is_file():
            try:
                Path(state_yaml_path).parent.mkdir(parents=True, exist_ok=True)
                Path(state_yaml_path).write_text(state_yaml_content, encoding="utf-8")
            except OSError:
                pass
        return {
            "cwd": str(data.get("cwd") or os.getcwd()),
            "schema": str(data.get("schema") or "research").strip(),
            "mcpServers": data.get("mcpServers") or [],
            "workflow": workflow,
        }
    except (OSError, json.JSONDecodeError):
        return None


def _delete_session_store(session_id: str) -> None:
    try:
        client = _redis_client()
        if client is not None:
            client.delete(_session_redis_key(session_id))
        else:
            _session_store_path(session_id).unlink(missing_ok=True)
    except OSError:
        pass


def _persisted_session_ids() -> list[str]:
    """Session ids present in the persistent store (survive restarts).

    Union with the in-memory sessions in ``session/list`` so a client can
    discover resumable sessions after a server restart (the advertised
    cross-process continuation feature).
    """
    client = _redis_client()
    if client is not None:
        try:
            keys = client.keys(_session_redis_key("*"))
        except OSError:
            return []
        return [k.rsplit(":", 1)[-1] for k in keys]
    try:
        return [p.stem for p in _session_store_dir().glob("*.json")]
    except OSError:
        return []


def _available_schemas() -> list[str]:
    """Installed workflow schema names (workflow yaml files in the pack)."""
    try:
        from orchestrator_next.paths import config_root
        wf_dir = config_root() / "workflows"
        if wf_dir.is_dir():
            return sorted(p.stem for p in wf_dir.glob("*.yaml"))
    except Exception:  # noqa: BLE001
        pass
    return ["research"]


# Keyword hints for request-driven schema routing. The FIRST matching schema
# whose keyword appears in the request wins; if none or several match, the
# server asks the user which workflow to run.
_SCHEMA_HINTS: dict[str, tuple[str, ...]] = {
    "research": ("research", "investigate", "findings", "report on"),
    "feature": ("feature", "new capability", "add ability"),
    "bugfix": ("bugfix", "bug fix", "fix bug", "defect", "crash"),
    "design": ("design", "architecture", "plan out", "blueprint"),
    "implement": ("implement", "implementing", "build it", "write code"),
    "patch": ("patch", "apply patch"),
    "complete": ("complete", "finish", "wrap up"),
}


def _route_schema(text: str) -> str | None:
    """Read the workflow schema the AGENT declared; None if not declared.

    The first step of any agent driving the orchestrator is to understand
    which workflow schema the request needs and SAY it — either as the first
    word ("research postgres indexing") or a "schema: X" declaration. The
    orchestrator NEVER guesses from synonyms: if the request doesn't
    explicitly name a schema, we ask the agent to declare one.
    """
    low = (text or "").strip().lower()
    if not low:
        return None
    first_word = low.split(maxsplit=1)[0].strip(" ,.:;")
    if first_word in _SCHEMA_HINTS:
        return first_word
    for prefix in ("schema:", "workflow:", "run the", "use the"):
        if low.startswith(prefix):
            rest = low[len(prefix):].strip()
            word = rest.split(maxsplit=1)[0].strip(" ,.:;\"'")
            if word in _SCHEMA_HINTS:
                return word
            # "run the research workflow on X" → "research" is not first
            # after "run the"; scan the next few tokens for a schema name.
            tokens = rest.split()
            for tok in tokens[:4]:
                if tok.strip(" ,.:;\"'") in _SCHEMA_HINTS:
                    return tok.strip(" ,.:;\"'")
    return None


def _ask_schema(session_id: str) -> dict:
    """Stream a schema-selection question back to the client."""
    schemas = ", ".join(_available_schemas())
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

    def handle(self, msg: dict) -> None:
        method = msg.get("method")
        message_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            _result(message_id, {
                "protocolVersion": 1,
                "capabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    # Workflow schema discovery — the AGENT's first step is to
                    # pick which workflow to run; the server only executes what
                    # the agent declares (never infers from request text).
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
            # Schema is the SERVER's decision, resolved from the request at
            # first session/prompt. An explicit env pin (ORCHESTRATOR_ACP_SCHEMA)
            # or client hint still wins; otherwise the schema stays unset and
            # the first prompt is routed (or asks if unclear).
            schema = str(
                params.get("schema")
                or os.environ.get("ORCHESTRATOR_ACP_SCHEMA", "")
                or ""
            ).strip()
            self.sessions[session_id] = {
                "cwd": params.get("cwd") or os.getcwd(),
                "mcpServers": params.get("mcpServers") or [],
                # Workflow schema (workflow yaml name) this session drives.
                # "" = unset → route from the first prompt's request.
                "schema": schema,
                # Set when we asked the user which workflow; the next prompt
                # text is the answer (e.g. "research <topic>").
                "awaiting_schema": False,
                # Multi-turn workflow state: {state_yaml_path, tmp_dir,
                # awaiting_step_id}. Cleared when the workflow completes or
                # the session is closed.
                "workflow": {},
            }
            _save_session(session_id, self.sessions[session_id])
            _result(message_id, {"sessionId": session_id})
            return

        if method == "session/load":
            session_id = params.get("sessionId")
            restored = _load_session(session_id) if session_id else None
            if restored is None:
                _error(message_id, -32002, f"unknown session: {session_id}")
                return
            self.sessions[session_id] = restored
            _result(message_id, {
                "sessionId": session_id,
                "cwd": restored.get("cwd"),
                "schema": restored.get("schema", "research"),
            })
            return

        if method == "session/prompt":
            session_id = params.get("sessionId")
            if session_id not in self.sessions:
                _error(message_id, -32001, f"unknown session: {session_id}")
                return
            prompt = params.get("prompt") or []
            text = " ".join(
                str(p.get("text", "")) for p in prompt if isinstance(p, dict)
            ).strip()
            if not text:
                _error(message_id, -32602, "empty prompt")
                return
            try:
                session = self.sessions[session_id]
                repo_root = str(session.get("cwd") or os.getcwd())

                # Request-driven schema routing (server-side decision). If the
                # schema isn't pinned yet and no workflow is in flight, resolve
                # it from the request text; if unclear, ask the user and wait
                # for their answer as the next prompt.
                if not session.get("schema") and not session.get("workflow", {}).get("state_yaml_path"):
                    route_text = _extract_topic(text)
                    routed = _route_schema(route_text)
                    if routed is None:
                        session["awaiting_schema"] = True
                        _save_session(session_id, session)
                        _result(message_id, _ask_schema(session_id))
                        return
                    session["schema"] = routed
                    session["awaiting_schema"] = False
                    _notify(session_id, f"📋 routing to workflow: {routed}")

                result = run_workflow(
                    text, session_id, repo_root,
                    schema=str(session.get("schema") or "research"),
                    session_state=session.setdefault("workflow", {}),
                )
                # Workflow complete → drop the persisted session (done).
                if not session.get("workflow", {}).get("state_yaml_path"):
                    _delete_session_store(session_id)
                else:
                    _save_session(session_id, session)
                _result(message_id, result)
            except Exception as exc:  # noqa: BLE001
                _error(message_id, -32603, f"workflow error: {exc}")
            return

        if method == "session/close":
            session_id = params.get("sessionId")
            if session_id in self.sessions:
                _cleanup_session(self.sessions[session_id])
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


def acp_run_main(argv: list[str]) -> int:
    """`orchestrator acp-run <topic>` — ACP client driver.

    Spawns the ACP server as a subprocess, performs initialize → session/new →
    session/prompt, forwards streamed session/update notifications to stdout
    live, and prints the final result. Exit 0 on completed, 1 on error.
    """
    if not argv:
        print("usage: orchestrator acp-run <topic>", file=sys.stderr)
        return 7
    topic = " ".join(argv).strip()

    proc = subprocess.Popen(
        [sys.executable, "-m", "orchestrator_next.acp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    next_id = 0

    def request(method: str, params: dict) -> tuple[dict, list[str]]:
        nonlocal next_id
        next_id += 1
        req_id = next_id
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        ) + "\n")
        proc.stdin.flush()
        updates: list[str] = []
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if msg.get("method") == "session/update":
                update = msg.get("params", {}).get("update", {})
                text = (update.get("content") or {}).get("text", "")
                if text:
                    print(text, flush=True)
                    updates.append(text)
                continue
            if msg.get("id") == req_id:
                return msg, updates
        return {"error": {"message": "server closed"}}, updates

    try:
        msg, _ = request("initialize", {
            "protocolVersion": 1, "clientCapabilities": {},
            "clientInfo": {"name": "orchestrator-acp-run", "version": "0.1"},
        })
        if msg.get("error"):
            print(f"error: initialize failed: {msg['error']}", file=sys.stderr)
            return 1
        msg, _ = request("session/new", {"cwd": os.getcwd(), "mcpServers": []})
        if msg.get("error") or not msg.get("result", {}).get("sessionId"):
            print(f"error: session/new failed: {msg}", file=sys.stderr)
            return 1
        session_id = msg["result"]["sessionId"]
        msg, _ = request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": topic}],
        })
        if msg.get("error"):
            print(f"error: session/prompt failed: {msg['error']}", file=sys.stderr)
            return 1
        outcome = msg.get("result", {}).get("outcome", {}).get("outcome")
        msgs = msg.get("result", {}).get("outcome", {}).get("messages", [])
        if msgs:
            text = msgs[0].get("content", [{}])[0].get("text", "")
            print("\n" + text)
        return 0 if outcome == "completed" else 1
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def main() -> int:
    server = AcpServer()
    for line in sys.stdin:
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
