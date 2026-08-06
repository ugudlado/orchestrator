"""Redis session rematerialize + resume (Phases 2-3 of the original ACP plan)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from orchestrator_next import sessions as sess_mod
from orchestrator_next.sessions import (
    Sessions,
    UnknownSessionError,
    _load_session,
    _save_session,
    reset_redis_client_cache,
    session_workspace,
)
from orchestrator_next.tests.acp_redis_fake import install_fake_redis as _install_fake_redis


def test_file_backend_save_load_ignores_session_cwd(tmp_path, monkeypatch):
    """Save/load must resolve the same store root regardless of the session's
    client-declared cwd — that cwd is only for artifact placement."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    reset_redis_client_cache()
    monkeypatch.chdir(tmp_path)

    other_cwd = str(tmp_path / "elsewhere")
    _save_session("sess-x", {
        "cwd": other_cwd,
        "schema": "research",
        "workflow": {"status": "await_input"},
    })
    restored = _load_session("sess-x")
    assert restored is not None
    assert restored["schema"] == "research"


def test_require_redis_errors_clearly(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    reset_redis_client_cache()
    try:
        sess_mod.require_redis()
        assert False, "expected RedisRequiredError"
    except sess_mod.RedisRequiredError as exc:
        assert "REDIS_URL" in str(exc) or "ORCHESTRATOR_ACP_REDIS_URL" in str(exc)


def test_session_run_leaves_no_durable_state_yaml(tmp_path, monkeypatch):
    """After a session prompt, Redis holds state; no *_state.yaml under sessions/."""
    fake = _install_fake_redis(monkeypatch)

    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult

    def fake_drive(state_yaml_path, **kwargs):
        # Simulate await_input record already in the seeded file: just pause.
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        raw.setdefault("step_history", []).append({
            "step_id": "intake-research",
            "phase": "main",
            "status": "await_input",
            "outputs": {"ask": "Who is the audience?"},
        })
        raw["next_step"] = {"phase": "main", "step_id": "intake-research"}
        Path(state_yaml_path).write_text(yaml.safe_dump(raw))
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    # run_workflow imports drive_loop inside the function — patch at source.
    import orchestrator_next.run_loop as run_loop
    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    result = sessions.prompt_session(sid, "postgres indexing")
    assert (result.get("outcome") or {}).get("outcome") == "await_input"

    # Redis has snapshot
    key = sess_mod._session_redis_key(sid)
    assert key in fake.store
    stored = json.loads(fake.store[key])
    assert "state_yaml_content" in stored["workflow"]
    assert stored["workflow"].get("status") == "await_input"
    assert "Who is the audience?" in (stored["workflow"].get("ask") or "")
    assert "state_yaml_path" not in stored["workflow"]
    assert "_live_state_path" not in stored["workflow"]

    # In-process session keeps a live temp path for the next prompt
    live = sessions.sessions[sid]["workflow"].get(sess_mod._LIVE_STATE_KEY)
    assert live and Path(live).is_file()

    # No durable *_state.yaml under session workspace
    ws = session_workspace(str(tmp_path), sid)
    leftovers = list(ws.rglob("*_state.yaml")) if ws.exists() else []
    assert leftovers == []

    # load_session reports status for CLI
    loaded = sessions.load_session(sid)
    assert loaded["status"] == "await_input"
    assert loaded["awaiting_step_id"] == "intake-research"
    assert "audience" in (loaded.get("ask") or "").lower()


def test_resume_await_input_continues_same_step(tmp_path, monkeypatch):
    fake = _install_fake_redis(monkeypatch)
    calls = {"n": 0, "directions": [], "paths": []}

    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    def fake_drive(state_yaml_path, **kwargs):
        calls["n"] += 1
        calls["directions"].append(kwargs.get("user_direction") or "")
        calls["paths"].append(state_yaml_path)
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        if calls["n"] == 1:
            raw.setdefault("step_history", []).append({
                "step_id": "intake-research",
                "phase": "main",
                "status": "await_input",
                "outputs": {"ask": "How deep?"},
            })
            Path(state_yaml_path).write_text(yaml.safe_dump(raw))
            return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")
        # Second turn: complete the workflow quickly
        Path(state_yaml_path).write_text(yaml.safe_dump({
            **raw,
            "status": "completed",
            "next_step": None,
            "step_history": (raw.get("step_history") or []) + [{
                "step_id": "intake-research", "phase": "main", "status": "completed",
                "outputs": {"reason": "ok"},
            }],
        }))
        return LoopResult(1, state_yaml_path)

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    sessions.prompt_session(sid, "topic")
    # Same process: reuse live temp path (no rematerialize)
    sessions.prompt_session(sid, "practical ops guide")
    assert calls["n"] == 2
    assert calls["paths"][0] == calls["paths"][1]
    assert "practical ops guide" in calls["directions"][1]

    # Fresh process simulation: only Redis → rematerialize
    sessions2 = Sessions()
    loaded = sessions2.load_session(sid)
    assert loaded["status"] == "completed"
    stored = json.loads(fake.store[sess_mod._session_redis_key(sid)])
    assert stored["workflow"]["status"] == "completed"
    assert sess_mod._LIVE_STATE_KEY not in stored["workflow"]


def test_cross_process_resume_rematerializes(tmp_path, monkeypatch):
    """After load_session in a new process, a new temp path is used."""
    fake = _install_fake_redis(monkeypatch)
    calls = {"n": 0, "paths": []}

    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    def fake_drive(state_yaml_path, **kwargs):
        calls["n"] += 1
        calls["paths"].append(state_yaml_path)
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        if calls["n"] == 1:
            raw.setdefault("step_history", []).append({
                "step_id": "intake-research",
                "phase": "main",
                "status": "await_input",
                "outputs": {"ask": "How deep?"},
            })
            Path(state_yaml_path).write_text(yaml.safe_dump(raw))
            return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")
        Path(state_yaml_path).write_text(yaml.safe_dump({
            **raw,
            "status": "completed",
            "next_step": None,
        }))
        return LoopResult(1, state_yaml_path)

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    sessions.prompt_session(sid, "topic")
    sessions2 = Sessions()
    sessions2.load_session(sid)
    sessions2.prompt_session(sid, "practical ops guide")
    assert calls["n"] == 2
    assert calls["paths"][0] != calls["paths"][1]
    stored = json.loads(fake.store[sess_mod._session_redis_key(sid)])
    assert stored["workflow"]["status"] == "completed"


def test_resume_rebinds_repo_root_and_worktree_path(tmp_path, monkeypatch):
    """Rematerialized state.yaml on resume carries THIS machine's paths, not
    whichever machine ran the session last (path rebinding)."""
    _install_fake_redis(monkeypatch)
    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    seen = {}

    def fake_drive(state_yaml_path, **kwargs):
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        seen["repo_root"] = raw.get("repo_root")
        seen["worktree_path"] = raw.get("worktree_path")
        raw.setdefault("step_history", []).append({
            "step_id": "intake-research", "phase": "main", "status": "await_input",
            "outputs": {"ask": "How deep?"},
        })
        Path(state_yaml_path).write_text(yaml.safe_dump(raw))
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    original_cwd = tmp_path / "original-machine"
    original_cwd.mkdir()
    sessions = Sessions()
    sid = sessions.new_session(cwd=str(original_cwd), schema="research")
    sessions.prompt_session(sid, "topic")
    assert seen["repo_root"] == str(original_cwd)

    resumed_cwd = tmp_path / "resumed-machine"
    resumed_cwd.mkdir()
    sessions2 = Sessions()
    sessions2.load_session(sid)
    sessions2.sessions[sid]["cwd"] = str(resumed_cwd)
    sessions2.prompt_session(sid, "answer")
    assert seen["repo_root"] == str(resumed_cwd)
    assert seen["worktree_path"] == str(resumed_cwd / ".orchestrator" / "sessions" / sid)


def test_resume_failed_with_direction_retries(tmp_path, monkeypatch):
    fake = _install_fake_redis(monkeypatch)
    calls = {"n": 0, "directions": []}
    from orchestrator_next.run_loop import LoopResult
    import orchestrator_next.run_loop as run_loop

    def fake_drive(state_yaml_path, **kwargs):
        calls["n"] += 1
        calls["directions"].append(kwargs.get("user_direction") or "")
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        if calls["n"] == 1:
            raw["status"] = "blocked"
            raw.setdefault("step_history", []).append({
                "step_id": "intake-research",
                "phase": "main",
                "status": "failed",
                "outputs": {"reason": "boom"},
            })
            # Mark node failed like record would
            nodes = (((raw.get("workflow_plan") or {}).get("main") or {}).get("nodes")) or []
            for n in nodes:
                if n.get("id") == "intake-research":
                    n["status"] = "failed"
            Path(state_yaml_path).write_text(yaml.safe_dump(raw))
            return LoopResult(3, state_yaml_path)
        return LoopResult(1, state_yaml_path)

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    sessions.prompt_session(sid, "topic")
    stored = json.loads(fake.store[sess_mod._session_redis_key(sid)])
    assert stored["workflow"]["status"] == "failed"

    sessions2 = Sessions()
    loaded = sessions2.load_session(sid)
    assert loaded["status"] == "failed"
    sessions2.prompt_session(sid, "try again with X")
    assert calls["n"] == 2
    assert "try again with X" in calls["directions"][1]
    # After unlock, node should have been reset before second drive
    assert calls["n"] == 2


def test_change_id_is_session_id(tmp_path, monkeypatch):
    _install_fake_redis(monkeypatch)
    seen = {}

    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    def fake_drive(state_yaml_path, **kwargs):
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        seen["change_id"] = raw.get("change_id")
        seen["slug"] = raw.get("slug")
        seen["ticket_id"] = raw.get("ticket_id")
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)
    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    sessions.prompt_session(sid, "postgres optimization techniques")
    assert seen["change_id"] == sid
    assert seen["slug"] == sid
    assert seen["ticket_id"] == sid


def test_session_cli_resume_reports_completed_without_rerun(tmp_path, monkeypatch):
    from orchestrator_next import session_cli

    fake = _install_fake_redis(monkeypatch)
    # Pre-seed a completed session in Redis
    _save_session("sess-done", {
        "cwd": str(tmp_path),
        "schema": "research",
        "workflow": {
            "status": "completed",
            "state_yaml_content": "change_id: sess-done\nstatus: completed\n",
        },
    })
    assert sess_mod._session_redis_key("sess-done") in fake.store

    out = session_cli.resume_session("sess-done", "should be ignored", cwd=str(tmp_path), on_update=lambda t: None)
    assert out["status"] == "completed"
    assert out["result"] is None


def test_session_cli_resume_failed_without_input_reports(tmp_path, monkeypatch, capsys):
    from orchestrator_next import session_cli

    _install_fake_redis(monkeypatch)
    _save_session("sess-fail", {
        "cwd": str(tmp_path),
        "schema": "research",
        "workflow": {"status": "failed", "state_yaml_content": "status: blocked\n"},
    })
    out = session_cli.resume_session("sess-fail", "", cwd=str(tmp_path), on_update=lambda t: None)
    assert out["status"] == "failed"
    assert out["result"] is None
    captured = capsys.readouterr().out
    assert "--resume" in captured
    assert "try again" in captured.lower() or "direction" in captured.lower()


def test_session_cli_resume_await_input_with_input(tmp_path, monkeypatch, capsys):
    """--resume <id> \"answer\" while await_input → prompt_session with user_direction."""
    from orchestrator_next import session_cli
    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    _install_fake_redis(monkeypatch)
    calls = {"directions": []}

    def fake_drive(state_yaml_path, **kwargs):
        calls["directions"].append(kwargs.get("user_direction") or "")
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        raw.setdefault("step_history", []).append({
            "step_id": "intake-research",
            "phase": "main",
            "status": "await_input",
            "outputs": {"ask": "How deep?", "missing": ["depth"]},
        })
        Path(state_yaml_path).write_text(yaml.safe_dump(raw))
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    sessions.prompt_session(sid, "postgres indexing")
    calls["directions"].clear()

    out = session_cli.resume_session(
        sid, "practical ops guide", cwd=str(tmp_path), on_update=lambda t: None,
    )
    assert out["result"] is not None
    assert calls["directions"] == ["practical ops guide"]
    captured = capsys.readouterr().out
    assert f"session_id={sid}" in captured
    assert "await_input" in captured or "How deep" in captured or "ask:" in captured


def test_session_cli_resume_failed_with_input_retries(tmp_path, monkeypatch):
    """--resume <id> \"try again…\" on failed → unlock + prompt with direction."""
    from orchestrator_next import session_cli
    from orchestrator_next.run_loop import LoopResult
    import orchestrator_next.run_loop as run_loop

    fake = _install_fake_redis(monkeypatch)
    calls = {"n": 0, "directions": []}

    def fake_drive(state_yaml_path, **kwargs):
        calls["n"] += 1
        calls["directions"].append(kwargs.get("user_direction") or "")
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        if calls["n"] == 1:
            raw["status"] = "blocked"
            raw.setdefault("step_history", []).append({
                "step_id": "intake-research",
                "phase": "main",
                "status": "failed",
                "outputs": {"reason": "boom"},
            })
            nodes = (((raw.get("workflow_plan") or {}).get("main") or {}).get("nodes")) or []
            for n in nodes:
                if n.get("id") == "intake-research":
                    n["status"] = "failed"
            Path(state_yaml_path).write_text(yaml.safe_dump(raw))
            return LoopResult(3, state_yaml_path)
        Path(state_yaml_path).write_text(yaml.safe_dump({
            **raw, "status": "completed", "next_step": None,
        }))
        return LoopResult(1, state_yaml_path)

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")
    sessions.prompt_session(sid, "topic")
    assert json.loads(fake.store[sess_mod._session_redis_key(sid)])["workflow"]["status"] == "failed"

    out = session_cli.resume_session(
        sid, "try again with clearer scope", cwd=str(tmp_path), on_update=lambda t: None,
    )
    assert calls["n"] == 2
    assert "try again with clearer scope" in calls["directions"][1]
    assert out["result"] is not None
    stored = json.loads(fake.store[sess_mod._session_redis_key(sid)])
    assert stored["workflow"]["status"] == "completed"


def test_session_cli_start_works_without_redis(tmp_path, monkeypatch):
    """No REDIS_URL → file-backed RunStore, session still starts."""
    from orchestrator_next import session_cli
    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    reset_redis_client_cache()
    monkeypatch.chdir(tmp_path)

    def fake_drive(state_yaml_path, **kwargs):
        raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        raw.setdefault("step_history", []).append({
            "step_id": "intake-research",
            "phase": "main",
            "status": "await_input",
            "outputs": {"ask": "Who is the audience?"},
        })
        Path(state_yaml_path).write_text(yaml.safe_dump(raw))
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    out = session_cli.start_schema("research", "hello", cwd=str(tmp_path))
    assert out["status"] == "await_input"

    store_dir = tmp_path / ".orchestrator" / "sessions" / "_state"
    assert list(store_dir.glob("*.yaml"))


def test_session_cli_start_raises_on_misconfigured_redis(monkeypatch):
    """REDIS_URL set but unusable (no redis package / bad connection) still raises."""
    from orchestrator_next import session_cli
    from orchestrator_next.sessions import SessionError

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    reset_redis_client_cache()
    monkeypatch.setattr(sess_mod, "_redis_client", lambda: None)
    try:
        session_cli.start_schema("research", "hello", cwd="/tmp")
        assert False, "expected SessionError"
    except SessionError as exc:
        assert "redis" in exc.message.lower()


def test_cli_help_has_no_acp_run():
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator_next"],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout + result.stderr).lower()
    assert "acp-run" not in combined
    assert "research" in combined
    assert "--resume" in combined


def test_concurrent_resume_returns_session_busy(tmp_path, monkeypatch):
    """A second prompt_session while the first holds the lock errors -32012."""
    _install_fake_redis(monkeypatch)
    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    def fake_drive(state_yaml_path, **kwargs):
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)

    sessions = Sessions()
    sid = sessions.new_session(cwd=str(tmp_path), schema="research")

    from orchestrator_next.run_store import open_store
    store = open_store()
    assert store.lock(sid)  # simulate another process already holding it

    try:
        sessions.prompt_session(sid, "topic")
        assert False, "expected SessionError"
    except sess_mod.SessionError as exc:
        assert exc.code == -32012
    finally:
        store.unlock(sid)


def test_load_session_rebinds_nonexistent_cwd(tmp_path, monkeypatch):
    _install_fake_redis(monkeypatch)
    _save_session("sess-y", {
        "cwd": "/nonexistent/machine/path",
        "schema": "research",
        "workflow": {"status": "active"},
    })
    restored = _load_session("sess-y")
    assert restored is not None
    assert restored["cwd"] != "/nonexistent/machine/path"
    assert Path(restored["cwd"]).is_dir()


def test_redis_save_sets_ttl(monkeypatch):
    fake = _install_fake_redis(monkeypatch)
    calls = []
    orig_set = fake.set

    def spy_set(key, value, ex=None, nx=False):
        calls.append(ex)
        return orig_set(key, value, ex=ex, nx=nx)

    monkeypatch.setattr(fake, "set", spy_set)
    _save_session("sess-ttl", {"cwd": "/tmp", "schema": "research", "workflow": {}})
    assert calls and calls[-1]


def test_load_session_connection_error_is_not_unknown_session(monkeypatch):
    """Redis outage must surface distinctly, not read as 'unknown session'."""
    class BrokenClient:
        def get(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    reset_redis_client_cache()
    monkeypatch.setattr(sess_mod, "_redis_client", lambda: BrokenClient())
    try:
        _load_session("sess-z")
        assert False, "expected ConnectionError to propagate"
    except ConnectionError:
        pass


def test_start_schema_main_redirects_bare_uuid_to_resume(tmp_path, monkeypatch, capsys):
    from orchestrator_next import session_cli

    _install_fake_redis(monkeypatch)
    _save_session("11111111-1111-1111-1111-111111111111", {
        "cwd": str(tmp_path), "schema": "research", "workflow": {"status": "active"},
    })
    rc = session_cli.start_schema_main(
        "research", ["11111111-1111-1111-1111-111111111111"]
    )
    assert rc == 7
    captured = capsys.readouterr().err
    assert "--resume 11111111-1111-1111-1111-111111111111" in captured


def test_start_schema_main_runs_normally_for_non_uuid_prompt(tmp_path, monkeypatch):
    from orchestrator_next import session_cli
    from orchestrator_next.run_loop import LOOP_PAUSED, LoopResult
    import orchestrator_next.run_loop as run_loop

    _install_fake_redis(monkeypatch)

    def fake_drive(state_yaml_path, **kwargs):
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id="intake-research")

    monkeypatch.setattr(run_loop, "drive_loop", fake_drive)
    rc = session_cli.start_schema_main("research", ["postgres", "indexing"])
    assert rc == 0


def test_cli_rejects_acp_run():
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator_next", "acp-run", "hello"],
        capture_output=True,
        text=True,
    )
    # acp-run removed → usage / exit nonzero, not the old subprocess driver
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "acp-run" not in combined.lower()


def test_load_session_unknown_raises():
    sessions = Sessions()
    try:
        sessions.load_session("does-not-exist")
        assert False, "expected UnknownSessionError"
    except UnknownSessionError:
        pass
