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
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

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

def _load_tavily_key() -> str:
    """TAVILY_API_KEY for the search-web step (falls back to ~/.hermes/.env)."""
    key = os.environ.get("TAVILY_API_KEY", "")
    if key:
        return key
    env_file = Path(os.path.expanduser("~/.hermes/.env"))
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TAVILY_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


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
    """
    text = prompt_text.strip()
    # Take the last "User:" section if present; else the whole text.
    marker = "\nUser:\n"
    idx = text.rfind(marker)
    if idx != -1:
        text = text[idx + len(marker):].strip()
    # Drop trailing instructions the client appends after the transcript.
    for cut in ("\nContinue the conversation", "\nAvailable tools"):
        pos = text.find(cut)
        if pos != -1:
            text = text[:pos].strip()
            break
    return text or "research"


def run_workflow(topic: str, session_id: str, repo_root: str) -> dict:
    """Run the configured research workflow through the real engine.

    Seeds a state.yaml for schema `research` in a temp repo, then drives
    dispatch → run_agent_step/run_script_step → record until complete,
    streaming each step as a session/update notification.
    """
    from orchestrator_next.run_loop import (
        run_agent_step,
        run_script_step,
        _seed_state,
    )
    from orchestrator_next.parser import load_state
    from orchestrator_next.record import record
    from orchestrator_next.paths import config_root

    topic = _extract_topic(topic)
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in topic.lower()).strip("-")
    if not slug:
        slug = "research"
    slug = slug[:80].rstrip("-")  # keep state filenames sane

    state_yaml_path = _seed_state(slug, "research", repo_root)
    _notify(session_id, f"🔍 Workflow: research on '{topic}'")
    _notify(session_id, f"  state: {state_yaml_path}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="orc-acp-"))
    try:
        state_yaml = state_yaml_path
        while True:
            if not Path(state_yaml).is_file():
                _notify(session_id, "✅ workflow complete (state archived)")
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
                break
            if code == 2:
                _notify(session_id, "⛔ workflow blocked")
                break
            if code == 3:
                _notify(session_id, "❌ workflow error")
                break

            step_id = action.get("step_id", "?")
            if action.get("model"):
                _notify(session_id, f"→ {step_id} (agent, {action.get('model')})")
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
            elif action.get("run"):
                _notify(session_id, f"→ {step_id} (script)")
                ok, state_yaml = run_script_step(action, state_yaml_path=state_yaml, state=state)
                _notify(session_id, f"  ✓ {step_id} done" if ok else f"  ✗ {step_id} failed")
                if not ok:
                    break
            else:
                _notify(session_id, f"→ {step_id} (no action)")
                break
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

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
                },
                "agentCapabilities": {},
                "serverInfo": {"name": "orchestrator", "version": "0.1.0"},
            })
            return

        if method == "session/new":
            session_id = str(uuid.uuid4())
            self.sessions[session_id] = {
                "cwd": params.get("cwd") or os.getcwd(),
                "mcpServers": params.get("mcpServers") or [],
            }
            _result(message_id, {"sessionId": session_id})
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
                repo_root = str(self.sessions[session_id].get("cwd") or os.getcwd())
                result = run_workflow(text, session_id, repo_root)
                _result(message_id, result)
            except Exception as exc:  # noqa: BLE001
                _error(message_id, -32603, f"workflow error: {exc}")
            return

        if method == "session/load":
            _error(message_id, -32002, "session/load not supported yet")
            return

        if method == "session/list":
            _result(message_id, {"sessionIds": list(self.sessions.keys())})
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
