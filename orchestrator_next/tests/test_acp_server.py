"""Tests for the ACP server (orchestrator_next.acp_server).

Covers the two critical review findings:
1. _extract_topic() must robustly pull the last user turn from ACP prompt
   text — supporting inline ("User: <text>"), block ("User:\\n<text>") and
   lowercase ("user: <text>") role markers — instead of returning the whole
   formatted prompt when the exact "\\nUser:\\n" marker is absent.
2. session/list must report persisted sessions (file/redis store), not just
   in-memory ones, so a client can discover resumable sessions after a server
   restart (the advertised cross-process continuation feature).
"""
from __future__ import annotations

import json

from orchestrator_next.acp_server import AcpServer, _extract_topic, _save_session


# ---------------------------------------------------------------------------
# Critical 1 — _extract_topic robustness
# ---------------------------------------------------------------------------

def test_extract_topic_inline_user_marker():
    """'User: <text>' inline after the colon — the reported bug."""
    prompt = (
        "System: you are a research assistant.\n"
        "Assistant: I can help.\n"
        "User: research postgres indexing"
    )
    assert _extract_topic(prompt) == "research postgres indexing"


def test_extract_topic_block_user_marker_still_works():
    """Legacy 'User:\\n<text>' block marker must keep working."""
    prompt = "System: ...\nUser:\nresearch postgres indexing"
    assert _extract_topic(prompt) == "research postgres indexing"


def test_extract_topic_takes_last_user_turn():
    """Multi-turn transcript — the LAST user message wins."""
    prompt = (
        "System: ...\n"
        "User: first question\n"
        "Assistant: first answer\n"
        "User: research postgres indexing"
    )
    assert _extract_topic(prompt) == "research postgres indexing"


def test_extract_topic_lowercase_user_marker():
    """Case-insensitive role label."""
    prompt = "System: ...\nuser: research postgres indexing"
    assert _extract_topic(prompt) == "research postgres indexing"


def test_extract_topic_no_marker_returns_whole_text():
    """No user marker → fall back to the whole (stripped) text."""
    prompt = "just a bare topic with no roles"
    assert _extract_topic(prompt) == "just a bare topic with no roles"


def test_extract_topic_strips_trailing_instructions():
    """Client-appended instructions after the user turn are dropped."""
    prompt = (
        "System: ...\n"
        "User: research postgres\n"
        "Continue the conversation or ask a follow-up."
    )
    assert _extract_topic(prompt) == "research postgres"


def test_extract_topic_empty_returns_research_fallback():
    assert _extract_topic("") == "research"
    assert _extract_topic("   \n  ") == "research"


# ---------------------------------------------------------------------------
# Critical 2 — session/list sees persisted sessions (restart discovery)
# ---------------------------------------------------------------------------

def test_session_list_includes_persisted_sessions(monkeypatch, tmp_path, capsys):
    """A session persisted by a PREVIOUS server process must be listed by a
    fresh AcpServer (empty in-memory state) — otherwise the cross-process
    continuation feature is undiscoverable after restart."""
    monkeypatch.setenv("ORCHESTRATOR_ACP_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    # Simulate a previous process that created + saved a session.
    _save_session("sess-restart-1", {
        "cwd": str(tmp_path),
        "schema": "research",
        "mcpServers": [],
        "workflow": {},
    })

    server = AcpServer()  # fresh process: no in-memory sessions
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "session/list", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "sess-restart-1" in out["result"]["sessionIds"]


def test_session_list_includes_in_memory_sessions(monkeypatch, tmp_path, capsys):
    """In-memory sessions (current process) still appear."""
    monkeypatch.setenv("ORCHESTRATOR_ACP_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    server = AcpServer()
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    session_id = out["result"]["sessionId"]

    server.handle({"jsonrpc": "2.0", "id": 3, "method": "session/list", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert session_id in out["result"]["sessionIds"]


def test_session_list_dedupes_persisted_and_memory(monkeypatch, tmp_path, capsys):
    """A session that is both in memory AND persisted appears exactly once."""
    monkeypatch.setenv("ORCHESTRATOR_ACP_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    _save_session("sess-both", {
        "cwd": str(tmp_path), "schema": "research",
        "mcpServers": [], "workflow": {},
    })

    server = AcpServer()
    server.sessions["sess-both"] = {"cwd": str(tmp_path), "schema": "research",
                                    "mcpServers": [], "workflow": {}}
    server.handle({"jsonrpc": "2.0", "id": 4, "method": "session/list", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    ids = out["result"]["sessionIds"]
    assert ids.count("sess-both") == 1
    assert "sess-both" in ids
