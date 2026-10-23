"""The seven cases the SQLite spike was tested with, against the dev workflow and the stub agent."""

from __future__ import annotations

import os
import signal
import time

FEATURE_GATES = ("design-signoff", "pr-signoff", "pr-merged")
HOTFIX = {"triage": {"change_type": "fix"}, "name-branch": {"change_type": "fix"}, "diagnose": {"fix_scope": "hotfix"}}


def alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_feature_path_three_gates(env):
    env.submit("feat")
    # a decision for an attempt that is already past: stale, never applied
    env.cli("decide", "feat", "design-signoff", 0, "rejected")
    env.start()
    for g in FEATURE_GATES:
        env.approve("feat", g)
    r = env.wait("feat", lambda r: r["status"] == "done")
    hist = r["outcome"]["history"]
    assert len(hist) == 21 and [h["gate"] for h in hist if h.get("gate")] == ["approved"] * 3
    assert r["loop"]["state"]["step_history"] == hist
    assert "event_stale" in env.kinds("feat")
    state = env.run_dir("feat") / "state.yaml"  # where the pack scripts and Studio read it
    assert state.exists() and "step_history" in state.read_text()


def test_hotfix_reject_with_reset_to(env):
    env.submit("fix", HOTFIX)
    env.start()
    env.at_gate("fix", "hotfix-signoff")
    env.cli("decide", "fix", "hotfix-signoff", 1, "rejected", "--reset-to", "diagnose", "--note", "look again")
    env.at_gate("fix", "hotfix-signoff", 2)
    hist = env.run("fix")["loop"]["state"]["step_history"]
    steps = [(h["step"], h["attempt"]) for h in hist]
    assert ("diagnose", 2) in steps and steps.index(("diagnose", 2)) > steps.index(("hotfix-signoff", 1))
    assert steps[-1] == ("diagnose", 2)  # back at the gate (attempt 2) with nothing recorded yet
    rejected = next(h for h in hist if h.get("gate") == "rejected")
    assert rejected["note"] == "look again"


def test_approval_before_gate_opens_is_used(env):
    env.submit("early")
    for g in FEATURE_GATES:  # all three sent before the worker even starts
        env.cli("decide", "early", g, 1, "approved")
    env.start()
    r = env.wait("early", lambda r: r["status"] == "done")
    assert len(r["outcome"]["history"]) == 21


def test_sigkill_mid_step_recovers(env):
    env.submit("crash", {"explore": {"_sleep": 8}})
    w = env.start()
    r = env.wait("crash", lambda r: r["current_step"] == "explore" and r["pid"])
    orphan = r["pid"]
    w.send_signal(signal.SIGKILL)
    w.wait()
    assert alive(orphan), "the child outlives its worker"
    t0 = time.time()
    env.start()
    while alive(orphan):
        assert time.time() - t0 < 5
        time.sleep(0.05)
    killed = time.time() - t0
    env.wait(
        "crash",
        lambda r: any(
            e["kind"] == "step_start" and e["data"]["step"] == "explore"
            for e in env.db.tail("crash", 500)[env.kinds("crash").index("recovered") :]
        ),
    )
    restarted = time.time() - t0
    print(f"\nRECOVERY orphan killed after {killed:.2f}s, step re-running after {restarted:.2f}s")
    assert killed < 3
    env.approve("crash", "design-signoff")
    log = env.db.tail("crash", 500)
    starts = [e["data"]["attempt"] for e in log if e["kind"] == "step_start" and e["data"]["step"] == "explore"]
    assert starts == [1, 1], "re-run keeps the attempt number, like an Inngest retry"
    assert "recovered" in env.kinds("crash")


def test_restart_while_waiting_at_gate(env):
    env.submit("wait")
    w = env.start()
    env.at_gate("wait", "design-signoff")
    w.send_signal(signal.SIGTERM)
    w.wait(timeout=5)
    env.start()
    time.sleep(1)
    assert env.run("wait")["status"] == "waiting"
    env.cli("decide", "wait", "design-signoff", 1, "approved")
    env.at_gate("wait", "pr-signoff")


def test_stop_kills_child_and_stays_stopped(env):
    env.submit("halt", {"explore": {"_sleep": 60}})
    w = env.start()
    r = env.wait("halt", lambda r: r["current_step"] == "explore" and r["pid"])
    pid = r["pid"]
    t0 = time.time()
    env.cli("stop", "halt")
    while alive(pid):
        assert time.time() - t0 < 3
        time.sleep(0.02)
    gone = time.time() - t0
    print(f"\nSTOP child gone after {gone:.2f}s")
    assert gone < 1
    r = env.wait("halt", lambda r: r["status"] == "stopped")
    assert r["current_step"] is None
    w.send_signal(signal.SIGTERM)
    w.wait(timeout=5)
    env.start()
    time.sleep(2)
    assert env.run("halt")["status"] == "stopped"
    assert env.kinds("halt").count("step_start") == env.kinds("halt").count("step_end") + 1  # explore never restarted


def test_needs_you_answer_reruns_with_user_answer(env):
    env.submit("ask", {"triage": {"change_type": "await_input"}})
    env.start()
    r = env.wait("ask", lambda r: r["status"] == "needs_you")
    w = r["waiting"]
    assert w["kind"] == "question" and w["step_id"] == "triage"
    env.cli("answer", "ask", "triage", w["wait"] + 5, "--answer", "stale")  # a wrong wait never answers
    env.cli("answer", "ask", "triage", w["wait"], "--answer", "it is a feature")
    env.at_gate("ask", "design-signoff")
    loop = env.run("ask")["loop"]
    assert "ORCHESTRATOR_USER_ANSWER" not in loop["extra_env"]  # the answer belongs to that one re-run
    triage = [h for h in loop["state"]["step_history"] if h["step"] == "triage"]
    assert [h["route"] for h in triage][-1] == "await_input" and len(triage) == 2
    assert [h["attempt"] for h in triage] == [1, 2] and loop["attempts"]["triage"] == 2  # counted once per run


def test_question_is_the_steps_own_ask(env):
    """A step that asks with an `ask` shows that question, not its summary."""
    env.submit("why", {"triage": {"change_type": "await_input", "ask": "Feature or fix?"}})
    env.start()
    r = env.wait("why", lambda r: r["status"] == "needs_you")
    assert r["waiting"]["question"] == "Feature or fix?"


def test_step_that_keeps_asking_ends_at_question_limit(env):
    """Every answer re-runs the step; one that asks again each time ends after max_question_reruns (3), not MAX_LOOPS."""
    env.submit("nag", {"triage": {"change_type": "await_input", "_keep_asking": True}})
    env.start()
    for _ in range(3):
        w = env.wait("nag", lambda r: r["status"] == "needs_you")["waiting"]
        env.cli("answer", "nag", "triage", w["wait"], "--answer", "go ahead")
        env.wait("nag", lambda r, n=w["wait"]: r["status"] != "needs_you" or r["waiting"]["wait"] != n)
    r = env.wait("nag", lambda r: r["status"] == "failed")
    assert r["outcome"]["status"] == "question_limit" and r["outcome"]["step_id"] == "triage"
    triage = [h for h in r["loop"]["state"]["step_history"] if h["step"] == "triage"]
    assert len(triage) == 4  # the first run and three answered re-runs


def test_question_count_restarts_when_step_is_reached_afresh(env):
    """Asks are counted per visit: after a gate sends the run back to the step, its count starts again."""
    env.submit("again", {"triage": {"change_type": "await_input"}})
    env.start()
    for visit in (1, 2):  # each visit: ask once, answer, the re-run completes
        w = env.wait("again", lambda r: r["status"] == "needs_you")["waiting"]
        assert w["step_id"] == "triage"
        env.cli("answer", "again", "triage", w["wait"], "--answer", "a feature")
        env.at_gate("again", "design-signoff", visit)
        assert env.run("again")["loop"]["asks"] == {"triage": 1}  # without the restart this visit would be 2
        if visit == 1:
            env.cli("decide", "again", "design-signoff", 1, "rejected", "--reset-to", "triage")
