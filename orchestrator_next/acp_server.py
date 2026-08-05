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

The demo workflow (research.yaml) runs a small deterministic research
pipeline: search the web for a topic (Tavily), summarize the findings, and
emit a final report. Each phase streams a session/update notification.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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
# Research workflow (small, deterministic)
# ---------------------------------------------------------------------------

def _load_tavily_key() -> str:
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


def _tavily_search(query: str, max_results: int = 4) -> list[dict]:
    """Search the web via Tavily. Returns [{title, url, content}]."""
    key = _load_tavily_key()
    if not key:
        return [{"title": "(no TAVILY_API_KEY)", "url": "", "content": ""}]
    payload = {
        "api_key": key,
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("results", []) or []
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return [{"title": f"(search error: {exc})", "url": "", "content": ""}]


def run_research(topic: str, session_id: str) -> dict:
    """Run the research pipeline, streaming each phase.

    Returns a completion result: {"outcome": "completed", "messages": [...]}.
    """
    _notify(session_id, f"🔍 Researching: {topic}")
    _notify(session_id, "  phase 1/3: searching the web (Tavily)...")

    results = _tavily_search(topic)
    top = results[:4]
    _notify(session_id, f"  phase 1/3: got {len(top)} results")

    # Phase 2 — summarize
    _notify(session_id, "  phase 2/3: summarizing findings...")
    lines = []
    for i, r in enumerate(top, 1):
        title = (r.get("title") or "(untitled)").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip().replace("\n", " ")[:200]
        lines.append(f"{i}. **{title}**\n   {url}\n   {snippet}...")
        _notify(session_id, f"  phase 2/3: source {i}: {title}")

    # Phase 3 — report
    _notify(session_id, "  phase 3/3: building report...")
    report = "\n\n".join(lines) if lines else "No results found."
    final_text = f"# Research: {topic}\n\n{report}"
    _notify(session_id, "✅ research complete")

    return {
        "outcome": {
            "outcome": "completed",
            "messages": [
                {"role": "assistant", "content": [{"type": "text", "text": final_text}]}
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
                result = run_research(text, session_id)
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
