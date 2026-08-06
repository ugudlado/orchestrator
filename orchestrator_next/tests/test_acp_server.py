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
from pathlib import Path

import yaml

from orchestrator_next.acp_server import (
    AcpServer,
    _extract_topic,
    _route_schema,
    _save_session,
)
from orchestrator_next.tests.acp_redis_fake import install_fake_redis as _install_fake_redis


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
# Schema routing — explicit declaration only (no synonym guessing)
# ---------------------------------------------------------------------------

def test_route_schema_first_word(monkeypatch):
    monkeypatch.setattr(
        "orchestrator_next.acp_server._available_schemas",
        lambda repo_root=None: ["research", "feature"],
    )
    assert _route_schema("research postgres indexing") == "research"
    assert _route_schema("feature add login") == "feature"


def test_route_schema_prefix_forms(monkeypatch):
    monkeypatch.setattr(
        "orchestrator_next.acp_server._available_schemas",
        lambda repo_root=None: ["research"],
    )
    assert _route_schema("schema: research postgres") == "research"
    assert _route_schema("workflow: research postgres") == "research"
    assert _route_schema("run the research workflow on indexing") is None


def test_route_schema_rejects_synonyms(monkeypatch):
    """Synonyms must NOT match — agent must declare a real schema name."""
    monkeypatch.setattr(
        "orchestrator_next.acp_server._available_schemas",
        lambda repo_root=None: ["research", "feature"],
    )
    assert _route_schema("investigate postgres indexing") is None
    assert _route_schema("fix the crash in auth") is None


def test_route_schema_empty():
    assert _route_schema("") is None
    assert _route_schema("   ") is None


def test_route_schema_unknown_installed_name(monkeypatch):
    """Names not in the installed pack must not route even if historically known."""
    monkeypatch.setattr(
        "orchestrator_next.acp_server._available_schemas",
        lambda repo_root=None: ["research"],
    )
    assert _route_schema("feature add login") is None


# ---------------------------------------------------------------------------
# Critical 2 — session/list sees persisted sessions (restart discovery)
# ---------------------------------------------------------------------------

def test_session_list_includes_persisted_sessions(monkeypatch, tmp_path, capsys):
    """A session persisted by a PREVIOUS server process must be listed by a
    fresh AcpServer (empty in-memory state) — otherwise the cross-process
    continuation feature is undiscoverable after restart."""
    _install_fake_redis(monkeypatch)

    # Simulate a previous process that created + saved a session.
    _save_session("sess-restart-1", {
        "cwd": str(tmp_path),
        "schema": "research",
        "workflow": {},
    })

    server = AcpServer()  # fresh process: no in-memory sessions
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "session/list", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "sess-restart-1" in out["result"]["sessionIds"]


def test_session_list_includes_in_memory_sessions(monkeypatch, tmp_path, capsys):
    """In-memory sessions (current process) still appear."""
    _install_fake_redis(monkeypatch)

    server = AcpServer()
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    session_id = out["result"]["sessionId"]

    server.handle({"jsonrpc": "2.0", "id": 3, "method": "session/list", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert session_id in out["result"]["sessionIds"]


def test_session_list_dedupes_persisted_and_memory(monkeypatch, tmp_path, capsys):
    """A session that is both in memory AND persisted appears exactly once."""
    _install_fake_redis(monkeypatch)

    _save_session("sess-both", {
        "cwd": str(tmp_path), "schema": "research",
        "workflow": {},
    })

    server = AcpServer()
    server.sessions["sess-both"] = {"cwd": str(tmp_path), "schema": "research",
                                    "workflow": {}}
    server.handle({"jsonrpc": "2.0", "id": 4, "method": "session/list", "params": {}})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    ids = out["result"]["sessionIds"]
    assert ids.count("sess-both") == 1
    assert "sess-both" in ids


def test_notify_includes_session_id(monkeypatch, tmp_path, capsys):
    """session/update notifications must carry sessionId for multi-session clients."""
    _install_fake_redis(monkeypatch)

    server = AcpServer()
    server.sessions["sess-n"] = {
        "cwd": str(tmp_path), "schema": "", "workflow": {},
    }
    # Undeclared schema → ask (emits a session/update notify).
    server.handle({
        "jsonrpc": "2.0", "id": 5, "method": "session/prompt",
        "params": {"sessionId": "sess-n", "prompt": [{"type": "text", "text": "hello"}]},
    })
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    notifies = [m for m in lines if m.get("method") == "session/update"]
    assert notifies
    assert notifies[0]["params"]["sessionId"] == "sess-n"


def test_drive_loop_pauses_on_await_input(tmp_path, monkeypatch):
    """drive_loop pauses after a step records status await_input (not contract flag)."""
    from orchestrator_next import spawn_resume
    from orchestrator_next.run_loop import LOOP_PAUSED, drive_loop

    sy = tmp_path / "state.yaml"
    sy.write_text(
        "change_id: t\nschema: research\nstatus: active\nphase: main\n"
        "repo_root: .\n"
        "workflow_plan:\n  main:\n    nodes:\n"
        "      - {id: ask, status: pending}\n"
        "next_step: {phase: main, step_id: ask}\n"
        "step_history: []\n"
    )

    action = {
        "step_id": "ask", "phase": "main", "model": "standard",
        "instruction": "ask", "attempt": 1,
        "step_context": {},
    }
    monkeypatch.setattr("orchestrator_next.run_loop.dispatch", lambda state, path: (action, 0))
    monkeypatch.setattr(spawn_resume, "apply_spawn_failure_resume", lambda *a, **k: None)

    def fake_agent_step(action, **kwargs):
        return {
            "step_id": "ask",
            "phase": "main",
            "status": "await_input",
            "agent": "standard",
            "outputs": {"ask": "Who is the audience?", "missing": ["audience"]},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr("orchestrator_next.run_loop.run_agent_step", fake_agent_step)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir(exist_ok=True)

    events = []
    result = drive_loop(
        str(sy),
        repo_root=str(tmp_path),
        models_yaml="",
        pause_on_await_input=True,
        on_event=lambda k, p: events.append((k, p)),
    )
    assert result.code == LOOP_PAUSED
    assert result.awaiting_step_id == "ask"
    assert any(k == "await_input" for k, _ in events)

    state = yaml.safe_load(sy.read_text())
    assert state["next_step"] == {"phase": "main", "step_id": "ask"}
    assert state["step_history"][-1]["status"] == "await_input"
    by_id = {n["id"]: n for n in state["workflow_plan"]["main"]["nodes"]}
    assert by_id["ask"]["status"] != "completed"


def test_available_schemas_queries_each_call(monkeypatch):
    from orchestrator_next import acp_server as acp
    import orchestrator_next.paths as paths_mod

    calls = {"n": 0}

    def fake_list(root=None):
        calls["n"] += 1
        return {"research": [("p", Path("."))], "feature": [("p", Path("."))]}

    monkeypatch.setattr(paths_mod, "list_workflows", fake_list)
    assert acp._available_schemas("/r") == ["feature", "research"]
    assert acp._available_schemas("/r") == ["feature", "research"]
    assert calls["n"] == 2
