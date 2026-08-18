"""End-to-end: a mock workflow, two real ACP agents, parallel dispatch, SQLite state.

Nothing is faked below the engine. The state is a real SQLite store, the
dispatcher is the real `dispatch_batch`, the recorder is the real `record`, and
the two agents are genuine ACP servers speaking JSON-RPC over stdio (see
`fake_acp_agent.py`) — they simply have no model behind them, so the whole thing
runs offline and free.

The workflow shape is a diamond, which is the point: the middle two steps have
no edge between them, so a correct parallel dispatcher must run them at the same
time, and a correct store must let both record without losing either.

        seed
        /  \\
    left    right      <- these two must overlap in wall-clock time
        \\  /
        join
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from orchestrator_next import acp_client
from orchestrator_next import state_store as ss
from orchestrator_next.dispatch import dispatch_batch
from orchestrator_next.parser import load_state
from orchestrator_next.readiness import ready_nodes
from orchestrator_next.record import record

FAKE_AGENT = Path(__file__).with_name("fake_acp_agent.py")

# Two distinct agents, so the test proves the transport is agent-agnostic
# rather than accidentally coupled to one implementation's timing.
AGENTS = {
    "agent-a": {"FAKE_ACP_USAGE": "1", "FAKE_ACP_PLAN": "1"},
    "agent-b": {"FAKE_ACP_USAGE": "1", "FAKE_ACP_ASK": "1"},
}

PLAN_NODES = [
    {"id": "seed", "depends_on": [], "status": "pending"},
    {"id": "left", "depends_on": ["seed"], "status": "pending"},
    {"id": "right", "depends_on": ["seed"], "status": "pending"},
    {"id": "join", "depends_on": ["left", "right"], "status": "pending"},
]

DOC = {
    "change_id": "orc-par", "slug": "orc-par", "ticket_id": "ORC-PAR",
    "schema": "mock", "config_pack": "workflows", "status": "active",
    "repo_root": "/repo", "phase": "main",
    "workflow_plan": {"main": {"nodes": PLAN_NODES, "filtered": []}},
    "step_history": [], "retries": {},
}


@pytest.fixture
def mock_pack(tmp_path, monkeypatch):
    """A four-step mock pack. Real contracts, so dispatch resolves them for real."""
    steps = tmp_path / "steps"
    for step_id in ("seed", "left", "right", "join"):
        d = steps / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            f"id: {step_id}\nversion: 1\nprompt: SKILL.md\n", encoding="utf-8"
        )
        (d / "SKILL.md").write_text(
            f"# {step_id}\n\nDo the {step_id} work and return a COMPLETION block.\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))
    # Every mock step routes to the same tier; the fake agents ignore it.
    routes = tmp_path / "models.yaml"
    routes.write_text(
        "tools:\n  mock:\n    binary: true\n"
        "models:\n  standard: {model_id: mock-model, tool: mock}\n"
        "step_models:\n" + "".join(
            f"  {s}: standard\n" for s in ("seed", "left", "right", "join")),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(routes))
    return steps


@pytest.fixture
def run_handle(tmp_path, mock_pack):
    handle = f"sqlite:///{tmp_path}/state.db#orc-par"
    store, h = ss.open_store(handle)
    store.create(h, json.loads(json.dumps(DOC)))
    return handle


def _turn(step_id: str, agent: str, hold: float = 0.0) -> dict:
    """Run one real ACP turn against a fake agent, returning a record payload."""
    env = dict(AGENTS[agent])
    if hold:
        env["FAKE_ACP_HANG"] = str(hold)
    env["FAKE_ACP_TEXT"] = (
        f"{step_id} handled by {agent}\n\n"
        "COMPLETION:\n"
        "  status: completed\n"
        "  outputs:\n"
        f'    reason: "{step_id} done via {agent}"\n'
    )
    started = datetime.now().isoformat()
    t0 = time.monotonic()
    norm = acp_client.run_turn(
        command=sys.executable, args=[str(FAKE_AGENT)],
        prompt=f"run step {step_id}", cwd=str(FAKE_AGENT.parent),
        model_id=f"mock-{agent}", env=env, timeout_s=60,
    )
    t1 = time.monotonic()
    return {
        "step_id": step_id, "phase": "main", "status": "completed",
        "agent": agent, "attempt": 1,
        "started_at": started, "ended_at": datetime.now().isoformat(),
        "usage": {
            "model": f"mock-{agent}",
            "input_tokens": norm["input_tokens"],
            "output_tokens": norm["output_tokens"],
            "cache_read_input_tokens": norm["cache_read_input_tokens"],
            "cache_creation_input_tokens": norm["cache_creation_input_tokens"],
            "cost_usd": 0.01, "duration_ms": int((t1 - t0) * 1000),
        },
        "outputs": {"reason": f"{step_id} done via {agent}"},
        "_wall": (t0, t1),
        "_text": norm["assistant_text"],
    }


def _record(handle: str, payload: dict) -> None:
    """record() with the conflict retry the parallel run loop uses."""
    body = {k: v for k, v in payload.items() if not k.startswith("_")}
    for _ in range(8):
        result, rc = record(handle, body)
        if rc != 4 or (result or {}).get("reason") != "state_write_conflict":
            assert rc == 0, (rc, result)
            return
        time.sleep(0.02)
    pytest.fail("record never landed")


# ---------------------------------------------------------------- the flow
def test_diamond_runs_left_and_right_in_parallel(run_handle):
    """The whole thing, start to finish, asserting the middle two overlap."""
    order: list[list[str]] = []
    spans: dict[str, tuple[float, float]] = {}
    agents = ["agent-a", "agent-b"]

    for _round in range(6):
        actions, code = dispatch_batch(run_handle, max_parallel=4)
        if code == 1:
            break
        assert code == 0, f"unexpected dispatch code {code}"
        assert actions, "code 0 must come with work"

        batch = [a["step_id"] for a in actions]
        order.append(batch)

        # left/right get DIFFERENT agents — the transport must not care.
        payloads = []
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            futures = [
                pool.submit(_turn, a["step_id"], agents[i % len(agents)], 0.4)
                for i, a in enumerate(actions)
            ]
            for f in futures:
                payloads.append(f.result())

        for p in payloads:
            spans[p["step_id"]] = p["_wall"]
            _record(run_handle, p)

    # --- the flow came out in the right shape -----------------------------
    assert order == [["seed"], ["left", "right"], ["join"]], order

    # --- and left/right genuinely overlapped in time ----------------------
    (l0, l1), (r0, r1) = spans["left"], spans["right"]
    overlap = min(l1, r1) - max(l0, r0)
    assert overlap > 0.2, (
        f"left and right did not run concurrently (overlap {overlap:.3f}s) — "
        f"left={l0:.3f}..{l1:.3f} right={r0:.3f}..{r1:.3f}"
    )

    # --- final state is consistent ----------------------------------------
    doc, _, _ = ss.load_doc(run_handle)
    statuses = {n["id"]: n.get("status") for n in doc["workflow_plan"]["main"]["nodes"]}
    assert statuses == {"seed": "completed", "left": "completed",
                        "right": "completed", "join": "completed"}
    assert len(doc["step_history"]) == 4, "no step outcome may be lost"
    assert {e["step_id"] for e in doc["step_history"]} == {"seed", "left", "right", "join"}

    _actions, code = dispatch_batch(run_handle, max_parallel=4)
    assert code == 1, "workflow should be complete"


def test_both_agents_actually_answered(run_handle):
    """Guard against the diamond passing because one agent did all the work."""
    a = _turn("left", "agent-a")
    b = _turn("right", "agent-b")
    assert "handled by agent-a" in a["_text"]
    assert "handled by agent-b" in b["_text"]
    # agent-b is the one that fires a permission request mid-turn.
    assert "[permission selected:y]" in b["_text"]
    # both report usage through the same typed path
    assert a["usage"]["input_tokens"] == b["usage"]["input_tokens"] == 1000


# ------------------------------------------------------- claim correctness
def test_a_claimed_step_is_not_handed_out_twice(run_handle):
    """The core parallel-safety property."""
    first, code = dispatch_batch(run_handle, max_parallel=4)
    assert code == 0 and [a["step_id"] for a in first] == ["seed"]

    # seed is now claimed (in_progress) but not recorded. A second dispatcher
    # must not hand it out again.
    state = load_state(run_handle)
    assert ready_nodes(state, exclude_claimed=True) == []
    assert ready_nodes(state) == ["seed"], "serial resume still sees it, by design"


def test_batch_claim_is_all_or_nothing(run_handle, tmp_path):
    """Two dispatchers racing the same ready set: one gets it, one re-reads."""
    _record(run_handle, _turn("seed", "agent-a"))

    results: list[list[str]] = []

    def grab() -> None:
        actions, code = dispatch_batch(run_handle, max_parallel=4)
        results.append([a["step_id"] for a in actions] if code == 0 else [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: grab(), range(2)))

    claimed = [r for r in results if r]
    assert len(claimed) == 1, f"exactly one dispatcher may win the batch: {results}"
    assert sorted(claimed[0]) == ["left", "right"]


def test_max_parallel_one_is_exactly_serial_dispatch(run_handle):
    """The back-compat hinge: parallelism is opt-in."""
    seen = []
    for _ in range(5):
        actions, code = dispatch_batch(run_handle, max_parallel=1)
        if code == 1:
            break
        assert len(actions) == 1, "max_parallel=1 must never batch"
        seen.append(actions[0]["step_id"])
        _record(run_handle, _turn(actions[0]["step_id"], "agent-a"))
    assert seen == ["seed", "left", "right", "join"]


# ----------------------------------------------------------- cost roll-up
def test_parallel_run_costs_are_all_in_the_index(run_handle, tmp_path):
    """Concurrency must not cost you the ledger."""
    import sqlite3

    for _round in range(6):
        actions, code = dispatch_batch(run_handle, max_parallel=4)
        if code == 1:
            break
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            payloads = list(pool.map(
                lambda a: _turn(a["step_id"], "agent-a"), actions))
        for p in payloads:
            _record(run_handle, p)

    conn = sqlite3.connect(tmp_path / "state.db")
    rows = conn.execute(
        "SELECT step_id, input_tokens, cost_usd FROM step_history "
        "WHERE run_id='orc-par' ORDER BY step_id"
    ).fetchall()
    total = conn.execute(
        "SELECT ROUND(SUM(cost_usd),4) FROM step_history WHERE run_id='orc-par'"
    ).fetchone()[0]
    conn.close()

    assert [r[0] for r in rows] == ["join", "left", "right", "seed"]
    assert all(r[1] == 1000 for r in rows), "ACP usage reached the index"
    assert total == 0.04
