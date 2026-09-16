"""End-to-end: a mock workflow, a fake agent runner, parallel dispatch, SQLite.

Re-covers the invariants that `test_parallel_acp_e2e.py` held before the ACP
transport was deleted. Nothing is faked below the engine: the state is a real
SQLite store, the dispatcher is the real `dispatch_batch`, the recorder is the
real `record`. Only the thing that would call a model — `run_loop.AGENT_RUNNER`
— is a stub, which is exactly the seam Phase 1.4 introduced.

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
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import yaml

from orchestrator_next import state_store as ss
from orchestrator_next.dispatch import dispatch_batch
from orchestrator_next.parser import load_state
from orchestrator_next.readiness import ready_nodes
from orchestrator_next.record import record

STEPS = ("seed", "left", "right", "join")

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
    for step_id in STEPS:
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
    routes = tmp_path / "models.yaml"
    routes.write_text(
        "models:\n  standard: {model_id: mock-model, tool: mock}\n"
        "step_models:\n" + "".join(f"  {s}: standard\n" for s in STEPS),
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


def _turn(step_id: str, hold: float = 0.0) -> dict:
    """One fake agent turn, returning a record payload plus its wall-clock span."""
    started = datetime.now().isoformat()
    t0 = time.monotonic()
    if hold:
        time.sleep(hold)
    t1 = time.monotonic()
    return {
        "step_id": step_id, "phase": "main", "status": "completed",
        "agent": "standard", "attempt": 1,
        "started_at": started, "ended_at": datetime.now().isoformat(),
        "usage": {
            "model": "mock-model",
            "input_tokens": 1000, "output_tokens": 50,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "cost_usd": 0.01, "duration_ms": int((t1 - t0) * 1000),
        },
        "outputs": {"reason": f"{step_id} done"},
        "_wall": (t0, t1),
    }


def _record(handle: str, payload: dict) -> None:
    """record() with the conflict retry the parallel run loop uses (CAS path)."""
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

    for _round in range(6):
        actions, code = dispatch_batch(run_handle, max_parallel=4)
        if code == 1:
            break
        assert code == 0, f"unexpected dispatch code {code}"
        assert actions, "code 0 must come with work"

        order.append([a["step_id"] for a in actions])

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            payloads = list(pool.map(lambda a: _turn(a["step_id"], 0.4), actions))

        for p in payloads:
            spans[p["step_id"]] = p["_wall"]
            _record(run_handle, p)

    assert order == [["seed"], ["left", "right"], ["join"]], order

    (l0, l1), (r0, r1) = spans["left"], spans["right"]
    overlap = min(l1, r1) - max(l0, r0)
    assert overlap > 0.2, (
        f"left and right did not run concurrently (overlap {overlap:.3f}s) — "
        f"left={l0:.3f}..{l1:.3f} right={r0:.3f}..{r1:.3f}"
    )

    doc, _, _ = ss.load_doc(run_handle)
    statuses = {n["id"]: n.get("status") for n in doc["workflow_plan"]["main"]["nodes"]}
    assert statuses == dict.fromkeys(STEPS, "completed")
    assert len(doc["step_history"]) == 4, "no step outcome may be lost"
    assert {e["step_id"] for e in doc["step_history"]} == set(STEPS)

    _actions, code = dispatch_batch(run_handle, max_parallel=4)
    assert code == 1, "workflow should be complete"


def test_agent_runner_is_the_only_spawn_seam(run_handle, monkeypatch):
    """`run_agent_step` reaches the model through AGENT_RUNNER and nothing else.

    With a runner installed, an agent step completes; with the default runner,
    it raises rather than silently spawning anything.
    """
    from orchestrator_next import run_loop

    seen: list[str] = []

    def fake(payload: dict) -> dict:
        seen.append(payload["step_id"])
        return {
            "assistant_text": (
                "COMPLETION:\n  status: completed\n  outputs:\n"
                f'    reason: "{payload["step_id"]} done"\n'
            ),
            **run_loop.ZEROED_USAGE,
        }

    actions, code = dispatch_batch(run_handle, max_parallel=1)
    assert code == 0
    action = actions[0]

    monkeypatch.setattr(run_loop, "AGENT_RUNNER", fake)
    payload = run_loop.run_agent_step(
        action, repo_root="/repo", models_yaml="",
        state_raw={}, state_yaml_path=run_handle,
    )
    assert seen == ["seed"]
    assert payload["status"] == "completed"

    monkeypatch.setattr(run_loop, "AGENT_RUNNER", run_loop._no_agent_runner)
    with pytest.raises(run_loop.NoAgentRunnerError):
        run_loop.run_agent_step(
            action, repo_root="/repo", models_yaml="",
            state_raw={}, state_yaml_path=run_handle,
        )


def test_agent_steps_actually_overlap_in_the_runner(run_handle, monkeypatch):
    """max_parallel=N runs N agent steps concurrently, through the real loop path.

    A threading barrier is the strict form of the assertion: if the two steps
    were serialized, the first would block forever and the barrier would time
    out rather than the test merely being slow.
    """
    from orchestrator_next import run_loop

    _record(run_handle, _turn("seed"))
    actions, code = dispatch_batch(run_handle, max_parallel=4)
    assert code == 0 and sorted(a["step_id"] for a in actions) == ["left", "right"]

    barrier = threading.Barrier(2, timeout=5)

    def fake(payload: dict) -> dict:
        barrier.wait()  # raises BrokenBarrierError if the steps are serialized
        return {
            "assistant_text": (
                "COMPLETION:\n  status: completed\n  outputs:\n"
                f'    reason: "{payload["step_id"]} done"\n'
            ),
            **run_loop.ZEROED_USAGE,
        }

    monkeypatch.setattr(run_loop, "AGENT_RUNNER", fake)

    def _run(act: dict) -> dict:
        return run_loop.run_agent_step(
            act, repo_root="/repo", models_yaml="",
            state_raw={}, state_yaml_path=run_handle,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        payloads = list(pool.map(_run, actions))

    assert [p["status"] for p in payloads] == ["completed", "completed"]


def test_concurrent_completions_do_not_lose_updates(run_handle):
    """Two steps recording at once: both land, via the CAS retry path."""
    _record(run_handle, _turn("seed"))
    actions, code = dispatch_batch(run_handle, max_parallel=4)
    assert code == 0

    start = threading.Barrier(len(actions), timeout=5)

    def _finish(act: dict) -> None:
        payload = _turn(act["step_id"])
        start.wait()  # force the writes to collide
        _record(run_handle, payload)

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        list(pool.map(_finish, actions))

    doc, _, _ = ss.load_doc(run_handle)
    recorded = {e["step_id"] for e in doc["step_history"]}
    assert recorded == {"seed", "left", "right"}, "a concurrent update was lost"
    statuses = {n["id"]: n.get("status") for n in doc["workflow_plan"]["main"]["nodes"]}
    assert statuses["left"] == statuses["right"] == "completed"


# ------------------------------------------------------- claim correctness
def test_a_claimed_step_is_not_handed_out_twice(run_handle):
    """The core parallel-safety property."""
    first, code = dispatch_batch(run_handle, max_parallel=4)
    assert code == 0 and [a["step_id"] for a in first] == ["seed"]

    state = load_state(run_handle)
    assert ready_nodes(state, exclude_claimed=True) == []
    assert ready_nodes(state) == ["seed"], "serial resume still sees it, by design"


def test_batch_claim_is_all_or_nothing(run_handle, monkeypatch):
    """Two dispatchers racing the same ready set: one gets it, one re-reads.

    Deterministic by construction. A barrier holds both dispatchers just after
    each has taken its compare-and-swap token and read the same snapshot, so
    both decide on version N and the loser necessarily attempts its claim after
    the winner has committed N+1. That is the exact interleaving that used to
    let both win: the claim re-read its own token, saw N+1, and saved cleanly.
    Without the fix this fails every run, not one in fifty.
    """
    _record(run_handle, _turn("seed"))

    import orchestrator_next.dispatch as dsp

    real_token = dsp.read_claim_token
    real_claim = dsp._claim_nodes

    decided = threading.Barrier(2, timeout=10)   # both hold a token for vN
    committed = threading.Event()                # the winner has written vN+1
    turn = threading.Semaphore(1)                # claims run one at a time
    seen_token = threading.local()

    def gated_token(path):
        token = real_token(path)
        if not getattr(seen_token, "done", False):
            seen_token.done = True
            try:
                decided.wait()
            except threading.BrokenBarrierError:
                pass
        return token

    def gated_claim(*args, **kwargs):
        # Serialize the two claims, and make the second one start strictly
        # after the first has committed. That is the interleaving the bug
        # needed: the loser decided on vN but claims against a store at vN+1.
        with turn:
            first = not committed.is_set()
            won = real_claim(*args, **kwargs)
            if first:
                committed.set()
            return won

    monkeypatch.setattr(dsp, "read_claim_token", gated_token)
    monkeypatch.setattr(dsp, "_claim_nodes", gated_claim)

    results: list[list[str]] = []
    lock = threading.Lock()

    def grab(_i) -> None:
        actions, code = dispatch_batch(run_handle, max_parallel=4)
        with lock:
            results.append([a["step_id"] for a in actions] if code == 0 else [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(grab, range(2)))

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
        _record(run_handle, _turn(actions[0]["step_id"]))
    assert seen == ["seed", "left", "right", "join"]


# ----------------------------------------------------------- cost roll-up
def test_parallel_run_costs_are_all_in_the_index(run_handle, tmp_path):
    """Concurrency must not cost you the ledger."""
    for _round in range(6):
        actions, code = dispatch_batch(run_handle, max_parallel=4)
        if code == 1:
            break
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            payloads = list(pool.map(lambda a: _turn(a["step_id"]), actions))
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
    assert all(r[1] == 1000 for r in rows), "every step's usage reached the index"
    assert total == 0.04


def test_script_steps_stay_serial(tmp_path, monkeypatch):
    """Script steps execute one at a time even when the DAG offers two.

    `dispatch_batch` may hand back two ready script actions, but `drive_loop`
    runs them in a plain loop while agent steps fan out to a thread pool. A
    script mutates the worktree, so overlapping two of them is the failure mode
    `worktree_lock.py` documents. The recorded concurrency here must stay 1.
    """
    from orchestrator_next import run_loop

    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)

    contracts = tmp_path / "c"
    for step_id in ("s-one", "s-two"):
        d = contracts / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            f"id: {step_id}\nversion: 2\nrun: script.sh\noutputs: []\n"
        )
        script = d / "script.sh"
        # Each script records its own overlap: bump a counter on entry, hold,
        # then write the max seen. Two overlapping scripts would log 2.
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'echo x >> "{tmp_path}/live"\n'
            "sleep 0.3\n"
            f'wc -l < "{tmp_path}/live" | tr -d " " >> "{tmp_path}/peak"\n'
            f': > "{tmp_path}/live"\n'
            "echo '{}'\n"
        )
        script.chmod(0o755)
    (tmp_path / "live").write_text("")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_MAX_PARALLEL", "4")

    sd = repo / ".orchestrator" / "ser"
    sd.mkdir(parents=True)
    sy = sd / "20260101T000000_feature_state.yaml"
    # No edge between the two: readiness offers both at once.
    sy.write_text(yaml.safe_dump({
        "change_id": "ser", "schema": "feature", "version": 1, "status": "active",
        "phase": "main", "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": [
            {"id": "s-one", "status": "pending"},
            {"id": "s-two", "status": "pending"},
        ]}},
        "step_history": [],
    }))

    code = run_loop.run_loop(str(sy), repo_root=str(repo), models_yaml="")
    assert code == 1, f"loop did not complete: {code}"

    peaks = [int(x) for x in (tmp_path / "peak").read_text().split()]
    assert peaks, "no script ran"
    assert max(peaks) == 1, f"script steps overlapped (peak concurrency {max(peaks)})"


# ------------------------------------------------- store compare-and-swap
@pytest.mark.parametrize("backend", ["sqlite", "file"])
def test_store_save_rejects_a_stale_token(tmp_path, backend):
    """The primitive the batch claim rests on: a token may be spent once.

    Two saves carrying the same token must not both land. Whatever the batch
    claim does above, if this breaks, parallel dispatch has no safety at all.
    """
    if backend == "sqlite":
        handle = f"sqlite:///{tmp_path}/cas.db#run-1"
    else:
        handle = str(tmp_path / "cas_state.yaml")

    store, h = ss.open_store(handle)
    store.create(h, {"change_id": "cas", "phase": "main", "n": 0})

    _doc, token = store.load(h)
    store.save(h, {"change_id": "cas", "phase": "main", "n": 1}, token)

    with pytest.raises(ss.StateConflictError):
        store.save(h, {"change_id": "cas", "phase": "main", "n": 2}, token)

    fresh, _ = store.load(h)
    assert fresh["n"] == 1, "the losing write must not have landed"
