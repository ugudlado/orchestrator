"""`orchestrator serve` — the HTTP layer over the protocol verbs.

Every route is meant to be framing and nothing else, so these tests assert two
things: the JSON a route returns is byte-for-byte what the corresponding
`protocol` function returns (no reshaping in the handler), and the two safety
rules hold — loopback-only binding and same-origin writes.

Requests go over a real socket through `http.client` against a live
ThreadingHTTPServer, because the origin check and the status framing live in
`BaseHTTPRequestHandler` and a direct function call would skip both.
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest
import yaml

from orchestrator_next import protocol, serve


@pytest.fixture
def server():
    """A live server on an ephemeral port, torn down with the test."""
    handler = type("Bound", (serve.Handler,), {})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    handler.origin = f"http://127.0.0.1:{httpd.server_port}"
    handler.token = "test-token"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _request(httpd, method: str, path: str, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
    payload = json.dumps(body) if body is not None else None
    hdrs = dict(headers or {})
    hdrs.setdefault("Host", f"127.0.0.1:{httpd.server_port}")
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")
    if method == "POST":
        hdrs.setdefault("X-Orchestrator-Token", "test-token")
    try:
        conn.request(method, path, body=payload, headers=hdrs)
        res = conn.getresponse()
        return res.status, res.read().decode()
    finally:
        conn.close()


def _json(httpd, method: str, path: str, body=None, headers=None):
    status, text = _request(httpd, method, path, body, headers)
    return status, json.loads(text)


# --- the page itself -------------------------------------------------------
def test_root_serves_the_ui_file(server) -> None:
    status, text = _request(server, "GET", "/")
    assert status == 200
    raw = serve.UI_FILE.read_text(encoding="utf-8")
    assert text == raw.replace(serve.TOKEN_PLACEHOLDER, "test-token")
    assert "<title>Orchestrator</title>" in text


def test_index_embeds_the_csrf_token(server) -> None:
    status, text = _request(server, "GET", "/")
    assert status == 200
    assert '<meta name="orchestrator-token" content="test-token">' in text
    assert serve.TOKEN_PLACEHOLDER not in text


def test_two_servers_get_different_tokens(monkeypatch) -> None:
    """Each process-lifetime `serve()` call mints its own token."""
    tokens: list[str] = []

    class FakeHTTPD:
        def __init__(self, addr, handler_cls):
            tokens.append(handler_cls.token)

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(serve, "ThreadingHTTPServer", FakeHTTPD)
    serve.serve("127.0.0.1", 0)
    serve.serve("127.0.0.1", 0)
    assert len(tokens) == 2
    assert tokens[0] != tokens[1]


def test_unknown_path_is_404(server) -> None:
    status, doc = _json(server, "GET", "/api/nope")
    assert status == 404
    assert "error" in doc


# --- reads mirror protocol exactly ----------------------------------------
_ROWS = [
    {"run_id": "r1", "slug": "alpha", "run_status": "active", "recipe": "feature",
     "current_step": "design", "started_at": "2026-09-18T10:00:00Z", "ended_at": None,
     "cost_usd": 1.25, "cost_partial": False, "nodes_done": 3, "nodes_total": 9,
     "archived": False, "stale": False, "last_activity": "2026-09-18T10:05:00Z"},
]


def test_runs_listing_is_protocol_runs(server, monkeypatch) -> None:
    seen: dict[str, int] = {}

    def fake_runs(limit=protocol.DEFAULT_RUN_LIMIT, *, all_ongoing=False):
        seen["limit"] = limit
        return list(_ROWS), 0

    monkeypatch.setattr(protocol, "runs", fake_runs)
    status, doc = _json(server, "GET", "/api/runs?limit=7")
    assert status == 200
    assert doc == _ROWS
    assert seen["limit"] == 7


def test_runs_listing_default_limit(server, monkeypatch) -> None:
    monkeypatch.setattr(
        protocol, "runs",
        lambda limit=protocol.DEFAULT_RUN_LIMIT, **_: ([{"limit": limit}], 0))
    _, doc = _json(server, "GET", "/api/runs")
    assert doc == [{"limit": protocol.DEFAULT_RUN_LIMIT}]


def test_run_detail_equals_protocol_status(server, monkeypatch) -> None:
    payload = {
        "run_id": "r1", "slug": "alpha", "run_status": "blocked", "phase": "design",
        "nodes": [{"id": "explore", "status": "completed", "cost_usd": 0.4}],
        "totals": {"cost_usd": 0.4, "seconds": 12.0},
        "gates": [], "gate_token": None, "artifacts": [], "usage": {},
    }
    monkeypatch.setattr(protocol, "status", lambda ref: (payload, 0))
    status, doc = _json(server, "GET", "/api/runs/alpha")
    assert status == 200
    assert doc == payload


def test_events_passes_the_step_filter(server, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_events(ref, *, since="", step=""):
        seen.update(ref=ref, step=step)
        return [{"step_id": step, "attempt": 1}], 0

    monkeypatch.setattr(protocol, "events", fake_events)
    status, doc = _json(server, "GET", "/api/runs/alpha/events?step=design")
    assert status == 200
    assert seen == {"ref": "alpha", "step": "design"}
    assert doc == [{"step_id": "design", "attempt": 1}]


def test_recipes_route(server, monkeypatch) -> None:
    rows = [{"name": "feature", "pack": "wf", "steps": 17, "gates": ["ship"], "inputs": {}}]
    monkeypatch.setattr(protocol, "recipes", lambda: (rows, 0))
    status, doc = _json(server, "GET", "/api/recipes")
    assert status == 200 and doc == rows


def test_missing_run_is_404(server, monkeypatch) -> None:
    def boom(ref):
        raise protocol.ProtocolError("no run 'ghost'")

    monkeypatch.setattr(protocol, "status", boom)
    status, doc = _json(server, "GET", "/api/runs/ghost")
    assert status == 404
    assert "ghost" in doc["error"]


def test_config_reports_settings_and_doctor(server) -> None:
    status, doc = _json(server, "GET", "/api/config")
    assert status == 200
    keys = {row["key"] for row in doc["settings"]}
    assert "serve.port" in keys        # the one key this feature added
    assert "run.max_parallel" in keys  # and the schema is whole
    for row in doc["settings"]:
        assert set(row) == {"key", "value", "source", "help"}
    assert any(line["name"] == "web ui" for line in doc["doctor"])


# --- writes ---------------------------------------------------------------
def test_approve_on_a_real_gate(server, monkeypatch, tmp_path) -> None:
    """A genuine pending gate, approved through the HTTP route.

    The gate record and its token are minted by `gates.issue_token`, so this
    exercises the real approval path rather than a stubbed one.
    """
    from orchestrator_next import gates

    state = tmp_path / "run_state.yaml"
    raw = {
        "run_id": "r1", "slug": "alpha", "status": "blocked", "schema": "mini",
        "phase": "main", "step_history": [],
        "workflow_plan": {"main": {"nodes": [
            {"id": "ship-signoff", "status": "blocked", "kind": "gate"},
        ]}},
    }
    record = gates.issue_token(raw, "ship-signoff", "ship_ok")
    state.write_text(yaml.safe_dump(raw), encoding="utf-8")

    monkeypatch.setattr(protocol, "resolve_run", lambda ref: str(state))
    monkeypatch.setattr(protocol, "_pin_config", lambda raw: None)
    monkeypatch.setattr(protocol, "_persist", lambda path: None)
    monkeypatch.setattr(protocol, "step", lambda path, **_: ({"status": "done"}, 0))

    status, doc = _json(
        server, "POST", "/api/runs/alpha/approve",
        {"token": record["token"], "edits": {"note": "looks good"}},
    )
    assert status == 200
    assert doc["gate_id"] == "ship-signoff"
    assert doc["token_name"] == "ship_ok"
    assert doc["edits"] == {"note": "looks good"}

    after = yaml.safe_load(state.read_text(encoding="utf-8"))
    assert after["gates"][0]["status"] == "approved"
    assert after["status"] == "active"


def test_bad_token_is_409(server, monkeypatch, tmp_path) -> None:
    state = tmp_path / "run_state.yaml"
    state.write_text(yaml.safe_dump(
        {"run_id": "r1", "slug": "a", "status": "blocked", "step_history": [],
         "phase": "main", "gates": []}), encoding="utf-8")
    monkeypatch.setattr(protocol, "resolve_run", lambda ref: str(state))
    monkeypatch.setattr(protocol, "_pin_config", lambda raw: None)

    status, doc = _json(server, "POST", "/api/runs/a/approve", {"token": "wrong"})
    assert status == 409
    assert "error" in doc


@pytest.mark.parametrize(
    "action, verb, body, expected",
    [
        ("cancel", "cancel", {}, ("alpha",)),
        ("retry", "reset_step", {"step_id": "design"}, ("alpha", "design")),
        ("resume", "resume", {"text": "option-b"}, ("alpha", "option-b")),
    ],
)
def test_action_routes_call_their_verb(
    server, monkeypatch, action, verb, body, expected
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        protocol, verb,
        lambda *args, **kw: (calls.append(args), ({"status": "ok"}, 0))[1])
    status, doc = _json(server, "POST", f"/api/runs/alpha/{action}", body)
    assert status == 200 and doc == {"status": "ok"}
    assert calls == [expected]


def test_start_seeds_only(server, monkeypatch) -> None:
    """POST /api/runs calls `start`, which seeds; the page never drives."""
    calls: list[tuple] = []

    def fake_start(recipe, slug, *, inputs=None, ticket_id=""):
        calls.append((recipe, slug, inputs, ticket_id))
        return {"run_id": "r9", "slug": slug, "state": "/tmp/x.yaml"}, 0

    monkeypatch.setattr(protocol, "start", fake_start)
    status, doc = _json(
        server, "POST", "/api/runs", {"recipe": "feature", "slug": "ORC-1"})
    assert status == 200 and doc["slug"] == "ORC-1"
    assert calls == [("feature", "ORC-1", None, "")]


def test_start_without_slug_is_409(server) -> None:
    status, doc = _json(server, "POST", "/api/runs", {"recipe": "feature"})
    assert status == 409
    assert "slug" in doc["error"]


# --- the two safety rules -------------------------------------------------
def test_cross_origin_post_is_refused(server, monkeypatch) -> None:
    """A page on another origin must not be able to approve or cancel."""
    called: list[str] = []
    monkeypatch.setattr(
        protocol, "cancel", lambda ref: (called.append(ref), ({}, 0))[1])

    status, doc = _json(
        server, "POST", "/api/runs/alpha/cancel", {},
        headers={"Origin": "https://evil.example"})
    assert status == 403
    assert "cross-origin" in doc["error"]
    assert called == []  # refused before the verb ran


def test_same_origin_post_is_allowed(server, monkeypatch) -> None:
    monkeypatch.setattr(protocol, "cancel", lambda ref: ({"status": "cancelled"}, 0))
    status, _doc = _json(
        server, "POST", "/api/runs/alpha/cancel", {},
        headers={"Origin": f"http://127.0.0.1:{server.server_port}"})
    assert status == 200


def test_oversized_body_is_refused(server) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request("POST", "/api/runs", body=b"{}", headers={
            "Host": f"127.0.0.1:{server.server_port}",
            "Content-Type": "application/json",
            "Content-Length": str(serve.MAX_BODY + 1),
            "X-Orchestrator-Token": "test-token",
        })
        assert conn.getresponse().status == 400
    finally:
        conn.close()


# --- DNS rebinding: Host-header allowlist ----------------------------------
def test_spoofed_host_header_is_refused_on_get(server) -> None:
    status, text = _request(server, "GET", "/", headers={"Host": "evil.example"})
    assert status == 421
    assert text == ""


def test_spoofed_host_header_is_refused_on_post(server, monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        protocol, "cancel", lambda ref: (called.append(ref), ({}, 0))[1])
    status, text = _request(
        server, "POST", "/api/runs/alpha/cancel", {}, headers={"Host": "evil.example"})
    assert status == 421
    assert text == ""
    assert called == []


def test_missing_host_header_is_refused(server) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        # http.client always injects Host unless we build the request by hand,
        # so send the request line and headers ourselves, without Host.
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("X-Orchestrator-Token", "test-token")
        conn.endheaders()
        res = conn.getresponse()
        assert res.status == 421
        res.read()
    finally:
        conn.close()


# --- CSRF token -------------------------------------------------------------
def test_post_without_token_is_403(server, monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        protocol, "cancel", lambda ref: (called.append(ref), ({}, 0))[1])
    status, text = _request(
        server, "POST", "/api/runs/alpha/cancel", {},
        headers={"X-Orchestrator-Token": ""})
    assert status == 403
    assert text == ""
    assert called == []


def test_post_with_correct_token_and_foreign_origin_is_403(server, monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        protocol, "cancel", lambda ref: (called.append(ref), ({}, 0))[1])
    status, doc = _json(
        server, "POST", "/api/runs/alpha/cancel", {},
        headers={"Origin": "https://evil.example"})
    assert status == 403
    assert "cross-origin" in doc["error"]
    assert called == []


def test_post_with_token_and_same_origin_succeeds(server, monkeypatch) -> None:
    monkeypatch.setattr(protocol, "cancel", lambda ref: ({"status": "cancelled"}, 0))
    status, doc = _json(server, "POST", "/api/runs/alpha/cancel", {})
    assert status == 200
    assert doc == {"status": "cancelled"}


def test_explicit_non_loopback_host_binds_with_a_warning(monkeypatch, capsys) -> None:
    """`--host 0.0.0.0` is allowed, because the person asked for it by name."""
    bound: list[tuple] = []
    monkeypatch.setattr(serve, "serve", lambda h, p, **kw: (bound.append((h, p)), 0)[1])
    assert serve.main(["--host", "0.0.0.0", "--port", "9000"]) == 0
    assert bound == [("0.0.0.0", 9000)]
    assert "no authentication" in capsys.readouterr().err


def test_non_loopback_default_host_is_refused(monkeypatch, capsys) -> None:
    """A non-loopback default (never typed by the user) never binds.

    The rule is about intent, not about the string: a host that arrived from
    anywhere but an explicit `--host` must be loopback or the server refuses.
    """
    monkeypatch.setattr(serve, "LOOPBACK", frozenset())  # make 127.0.0.1 "non-loopback"
    bound: list[tuple] = []
    monkeypatch.setattr(serve, "serve", lambda h, p, **kw: (bound.append((h, p)), 0)[1])
    assert serve.main([]) == 3
    assert bound == []
    assert "refusing to bind" in capsys.readouterr().err


def test_unknown_flag_is_rejected(capsys) -> None:
    assert serve.main(["--wat"]) == 3
    assert "unknown argument" in capsys.readouterr().err


def test_help_does_not_bind(capsys) -> None:
    assert serve.main(["--help"]) == 0
    assert "orchestrator serve" in capsys.readouterr().out


def test_port_comes_from_settings(monkeypatch) -> None:
    """`[serve] port` is read through the settings schema, not hard-coded."""
    from orchestrator_next import settings

    assert settings.spec_for("serve.port").default == 8765
    bound: list[tuple] = []
    monkeypatch.setattr(settings, "get", lambda dotted: 9999)
    monkeypatch.setattr(serve, "serve", lambda h, p, **kw: (bound.append((h, p)), 0)[1])
    assert serve.main([]) == 0
    assert bound == [("127.0.0.1", 9999)]


def test_explicit_port_flag_beats_settings(monkeypatch) -> None:
    bound: list[tuple] = []
    monkeypatch.setattr(serve, "serve", lambda h, p, **kw: (bound.append((h, p)), 0)[1])
    assert serve.main(["--port", "1234"]) == 0
    assert bound == [("127.0.0.1", 1234)]
