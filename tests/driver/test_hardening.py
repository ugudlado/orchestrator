"""Real exec scripts, gate edge cases, timeouts, refusals, engine errors, and the driver's own file handling."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from test_workflows_e2e import drive

from orchestrator_driver import steps
from orchestrator_driver.workers import claude


def test_dev_with_real_exec_scripts(make_env, pack):
    """Judgment steps are stubbed; every exec script of the pack runs for real against a git repo."""
    env = make_env(stub_exec=False)
    env.submit("real", workflow="dev")
    res = drive(env, "real")
    r = res["run"]
    assert r["status"] == "done", str(r["outcome"])[:1500]
    hist = r["outcome"]["history"]
    execs = {h["step"]: h for h in hist if h.get("kind") == "exec"}
    assert {"create-worktree", "remove-worktree", "persist-learnings", "workflow-report"} <= set(execs)
    assert all(h["exit_code"] == 0 for h in execs.values()), execs
    state = r["loop"]["state"]
    # state_mutating readback: the script's state_patch / edits reached the driver's state
    assert state["schema"] == "dev"  # seeded, as the pack's driver notes ask
    # (the pack's create-worktree reads only the first line of its state query, so its branch is always
    # feature/<slug> whatever the schema: a known pack bug in docs/pack-driver-notes.md)
    assert state["branch"].endswith("/real") and state["worktree_path"] == str(env.tmp / "worktrees" / "real")
    assert not Path(state["worktree_path"]).exists()  # remove-worktree removed it
    wts = subprocess.run(
        ["git", "-C", str(env.project), "worktree", "list"], capture_output=True, text=True, check=True
    ).stdout
    assert len(wts.strip().splitlines()) == 1
    # scripts committed without the exec bit ran through bash
    plain = [
        d.name for d in (Path(pack) / "steps").iterdir() if d.name in execs and not os.access(d / "script.sh", os.X_OK)
    ]
    print(f"\nREAL EXEC steps={sorted(execs)} non-executable={plain}")
    # the driver's own files live outside results/ and outside the agent's reach
    drv = env.run_dir("real") / "driver"
    assert stat.S_IMODE(drv.stat().st_mode) == 0o700 and any(drv.glob("*.stdout"))
    assert not list((env.run_dir("real") / "results").glob("*.stdout"))
    assert not list((env.run_dir("real") / "results").glob("*.lock"))


def test_gate_rejected_without_reset_to_and_duplicate_approvals(env):
    env.submit("rej")
    env.start()
    env.at_gate("rej", "design-signoff")
    env.cli("decide", "rej", "design-signoff", 1, "rejected", "--note", "no")
    # rejected without reset_to and no on_failure edge: the engine has nowhere to send it, so a person is asked
    q = env.wait("rej", lambda r: r["status"] == "needs_you")["waiting"]
    assert q["step_id"] == "design-signoff" and "no on_failure" in q["reason"]
    env.cli("answer", "rej", "design-signoff", q["wait"], "--reset-to", "design")  # the answer names the rework
    env.at_gate("rej", "design-signoff", 2)
    assert env.run("rej")["loop"]["attempts"]["design-signoff"] == 2
    hist = env.run("rej")["loop"]["state"]["step_history"]
    assert [h["gate"] for h in hist if h.get("gate")] == ["rejected"]
    assert ("design", 2) in [(h["step"], h["attempt"]) for h in hist]
    env.cli("decide", "rej", "design-signoff", 2, "approved")
    env.cli("decide", "rej", "design-signoff", 2, "approved")  # a duplicate: stale once the first is used
    env.cli("decide", "rej", "design-signoff", 2, "rejected")
    env.approve("rej", "pr-signoff")
    env.approve("rej", "pr-merged")
    r = env.wait("rej", lambda r: r["status"] == "done")
    approvals = [h for h in r["outcome"]["history"] if h.get("gate") == "approved"]
    assert len(approvals) == 3
    assert env.db.open_events("rej") == []  # nothing left dangling
    assert env.kinds("rej").count("event_stale") >= 2


def test_gate_timeout(make_env):
    env = make_env(gate_timeout_days=0.00002)  # about 2 s
    env.submit("late")
    env.start()
    t0 = time.time()
    while time.time() - t0 < 60 and env.run("late")["status"] not in ("failed", "done"):
        time.sleep(0.1)
    r = env.run("late")
    assert r["status"] == "failed" and r["outcome"]["status"] == "gate_timeout"
    assert r["outcome"]["gate"] == "design-signoff" and r["waiting"] is None


def refusing_pack(tmp: Path, pack: str) -> Path:
    """A pack that shares the real steps but has a workflow whose `implement` needs a token nobody issued."""
    out = tmp / "refusing-pack"
    out.mkdir()
    for item in Path(pack).iterdir():
        if item.name != "workflows":
            (out / item.name).symlink_to(item)
    (out / "workflows").mkdir()
    (out / "workflows" / "tok.yaml").write_text(
        yaml.safe_dump({"name": "tok", "steps": ["check-rerun", {"id": "implement", "requires": "impl_token"}]})
    )
    return out


def test_refused_when_token_missing(make_env, pack, tmp_path):
    env = make_env(pack=str(refusing_pack(tmp_path, pack)))
    env.submit("nope", workflow="tok")
    env.start()
    r = env.wait("nope", lambda r: r["status"] == "failed")
    assert r["outcome"]["status"] == "refused" and r["outcome"]["missing_token"] == "impl_token"
    assert r["outcome"]["step_id"] == "implement"


def test_engine_error_is_reported_as_failed_once(env):
    # the agent returns a verdict outside the contract's enum: the engine answers `error`, the driver reports
    # the step failed, and design-review's on_failure routing (back to design) takes over instead of the run dying
    env.submit("bad", {"design-review": {"verdict": "not-a-verdict"}})
    env.start()
    r = env.wait("bad", lambda r: r["status"] in ("needs_you", "failed", "done"))
    log = env.db.tail("bad", 1000)
    first = next(e for e in log if e["kind"] == "engine_error")
    assert first["data"]["step"] == "design-review" and first["data"]["attempt"] == 1
    nxt = next(e for e in log if e["kind"] == "next" and e["id"] > first["id"])["data"]
    assert (nxt["status"], nxt["step_id"], nxt["route"]) == ("ready", "design", "on_failure")
    hist = r["loop"]["state"]["step_history"]
    assert any(h["step"] == "design-review" and h["status"] == "failed" and h.get("engine_error") for h in hist)
    # it keeps failing, so the engine's own retry limit ends it with a question, not the driver's error path
    assert r["status"] == "needs_you" and r["outcome"] is None
    assert [e["data"]["attempt"] for e in log if e["kind"] == "engine_error"][:2] == [1, 2]


def test_garbage_event_numbers_do_not_crash_the_worker(env):
    env.submit("junk")
    env.start()
    env.at_gate("junk", "design-signoff")
    for bad in ("one", None, [1], {"x": 1}, 1.5, True, "1e3"):
        env.db.add_event("junk", "gate.decided", {"gate": "design-signoff", "attempt": bad, "decision": "approved"})
    env.db.add_event("junk", "gate.decided", "not even an object")  # type: ignore[arg-type]
    env.db.add_event("junk", "input.provided", {"step_id": "x", "wait": "n/a"})
    env.cli("decide", "junk", "design-signoff", "1", "approved")  # a numeric string is fine
    env.at_gate("junk", "pr-signoff")
    assert env.workers[0].poll() is None
    assert env.kinds("junk").count("event_stale") >= 6


def test_stop_leaves_state_and_notification(make_env, tmp_path):
    out = tmp_path / "notes.jsonl"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ncat >> {out}; echo >> {out}\n")
    hook.chmod(0o755)
    env = make_env(notify_cmd=str(hook))
    env.submit("halt2")
    env.start()
    env.at_gate("halt2", "design-signoff")
    env.cli("decide", "halt2", "design-signoff", 1, "approved")  # left open, then stopped
    env.cli("stop", "halt2")
    r = env.wait("halt2", lambda r: r["status"] == "stopped")
    assert r["outcome"]["history"] and r["outcome"]["status"] == "stopped"
    assert "step_history" in (env.run_dir("halt2") / "state.yaml").read_text()
    deadline = time.time() + 10
    while time.time() < deadline and '"finished"' not in (out.read_text() if out.exists() else ""):
        time.sleep(0.1)
    notes = [json.loads(x) for x in out.read_text().split("\n") if x.strip()]
    assert any(n["kind"] == "gate" and n["step_id"] == "design-signoff" for n in notes)
    assert any(n["kind"] == "finished" and n["status"] == "stopped" for n in notes)
    assert env.db.open_events("halt2") == []


def test_slow_notify_does_not_stall_the_run(make_env, tmp_path):
    env = make_env(notify_cmd="sleep 20")
    env.submit("slowhook")
    env.start()
    t0 = time.time()
    env.at_gate("slowhook", "design-signoff")
    assert time.time() - t0 < 15


# --- the driver's own files and the process-group kill ---------------------------------------


def test_write_private_refuses_symlinks(tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        steps.write_private(link, "clobber")
    assert target.read_text() == "keep"
    steps.write_private(tmp_path / "ok", "x")
    assert stat.S_IMODE((tmp_path / "ok").stat().st_mode) == 0o600


def test_read_result_is_strict(tmp_path):
    good = tmp_path / "r.json"
    good.write_text('{"status": "completed"}')
    assert steps.read_result(good) == {"status": "completed"}
    assert steps.read_result(tmp_path / "missing.json") is None
    link = tmp_path / "l.json"
    link.symlink_to(good)
    with pytest.raises(ValueError):
        steps.read_result(link)
    big = tmp_path / "big.json"
    big.write_text('{"a": "' + "x" * 2_000_000 + '"}')
    with pytest.raises(ValueError):
        steps.read_result(big)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises((ValueError, OSError)):
        steps.read_result(fifo)
    arr = tmp_path / "arr.json"
    arr.write_text("[1]")
    with pytest.raises(TypeError):
        steps.read_result(arr)


def test_kill_group_checks_what_it_kills(tmp_path):
    state = tmp_path / "state.yaml"
    other = tmp_path / "other.yaml"
    sleeper = ["sleep", "30"]

    def spawn(state_path: Path, **kw):
        return subprocess.Popen(sleeper, env={**os.environ, "STATE_YAML_PATH": str(state_path)}, **kw)

    mine = spawn(state, start_new_session=True)  # a group leader for this run's state file
    wrong_run = spawn(other, start_new_session=True)
    not_leader = spawn(state)  # same state file, but in our own group
    try:
        assert not steps.kill_group(wrong_run.pid, state)  # another run's process
        assert not steps.kill_group(not_leader.pid, state)  # does not lead its group
        assert not steps.kill_group(os.getpid(), state)
        assert not steps.kill_group(os.getpgrp(), state) and not steps.kill_group(1, state)
        assert not steps.kill_group(None, state) and not steps.kill_group(2**22 + 12345, state)
        assert wrong_run.poll() is None and not_leader.poll() is None
        assert steps.kill_group(mine.pid, state)
        assert mine.wait(timeout=5) == -signal.SIGKILL
    finally:
        for p in (mine, wrong_run, not_leader):
            p.kill()
            p.wait()


def test_sandbox_denies_git_hooks_and_config(tmp_path, monkeypatch):
    from conftest import git_repo

    repo = git_repo(tmp_path / "r")
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "b", str(wt)], check=True)
    monkeypatch.chdir(wt)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_ARTIFACTS_DIR", str(tmp_path / "art"))
    cfg = claude.sandbox_settings(str(tmp_path / "res" / "x.json"))
    deny = cfg["sandbox"]["filesystem"]["denyWrite"]
    common = os.path.realpath(repo / ".git")
    gitdir = os.path.realpath(repo / ".git" / "worktrees" / "wt")
    for want in (f"{common}/config", f"{common}/hooks", f"{gitdir}/config", f"{gitdir}/hooks"):
        assert want in deny, (want, deny)
    assert not any(
        d in (common, f"{common}/objects", f"{common}/refs", f"{common}/index") for d in deny
    )  # committing works
    edit = cfg["permissions"]["deny"]
    assert any(r.startswith("Edit(") and r.endswith("/hooks/**)") for r in edit)
    # driver files are neither writable nor added as a directory to the agent
    assert all("/driver" not in w for w in cfg["sandbox"]["filesystem"]["allowWrite"])


def test_exec_git_runs_without_hooks(make_env):
    """The env exec scripts get makes git ignore a planted hook and fsmonitor."""
    env = make_env()
    repo = env.project
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {env.tmp}/hook-ran\n")
    hook.chmod(0o755)
    from orchestrator_driver.gitenv import GIT_ENV

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "other"], check=True, env={**os.environ, **GIT_ENV})
    assert not (env.tmp / "hook-ran").exists()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    assert (env.tmp / "hook-ran").exists()  # control: without the env the hook does run


def test_sigkill_recovery_kills_by_db_pid_only(env):
    env.submit("crash2", {"explore": {"_sleep": 8}})
    w = env.start()
    r = env.wait("crash2", lambda r: r["current_step"] == "explore" and r["pid"])
    assert r["started_at"]
    pid = r["pid"]
    w.send_signal(signal.SIGKILL)
    w.wait()
    assert not list((env.run_dir("crash2") / "results").glob("*.lock"))
    env.start()
    t0 = time.time()
    while time.time() - t0 < 10:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("orphan not killed")
    rec = next(e for e in env.db.tail("crash2", 500) if e["kind"] == "recovered")["data"]
    assert rec["killed"] is True
    assert env.run("crash2")["pid"] != pid
