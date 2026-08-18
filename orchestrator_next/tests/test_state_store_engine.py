"""The engine, driven against a SQLite-backed run instead of a state.yaml file.

This is the test that matters: it calls the real `record()` and the real
`load_state()` / `dispatch()` with a `sqlite://` handle and asserts the run
advances exactly as it does on a file. If this passes, the store is not a
parallel implementation — it is the same engine with a different backend.
"""
from __future__ import annotations

import json

import pytest

from orchestrator_next import state_store as ss
from orchestrator_next.dispatch import dispatch
from orchestrator_next.parser import load_state
from orchestrator_next.record import record

BASE = {
    "change_id": "orc-901",
    "slug": "orc-901",
    "ticket_id": "ORC-901",
    "schema": "feature",
    "config_pack": "workflows",
    "status": "active",
    "repo_root": "/repo",
    "phase": "main",
    "workflow_plan": {"main": {"nodes": [
        {"id": "explore", "depends_on": [], "status": "pending"},
        {"id": "design", "depends_on": ["explore"], "status": "pending"},
    ]}},
    "step_history": [],
    "retries": {},
}


def _payload(step_id: str, status: str = "completed") -> dict:
    return {
        "step_id": step_id,
        "phase": "main",
        "status": status,
        "agent": "claude",
        "attempt": 1,
        "started_at": "2026-08-18T10:00:00+00:00",
        "ended_at": "2026-08-18T10:05:00+00:00",
        "usage": {"model": "claude-sonnet-5", "input_tokens": 1000,
                  "output_tokens": 200, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0, "cost_usd": 0.006,
                  "duration_ms": 300000},
        "outputs": {"reason": f"{step_id} finished"},
    }


@pytest.fixture
def sqlite_handle(tmp_path):
    handle = f"sqlite:///{tmp_path}/state.db#orc-901"
    store, h = ss.open_store(handle)
    store.create(h, json.loads(json.dumps(BASE)))
    return handle


def test_load_state_reads_a_sqlite_run(sqlite_handle):
    state = load_state(sqlite_handle)
    assert state.change_id == "orc-901"
    assert state.phase == "main"
    assert [n["id"] for n in state.workflow_plan["main"]["nodes"]] == ["explore", "design"]


def test_dispatch_picks_the_first_ready_node_from_a_sqlite_run(sqlite_handle):
    state = load_state(sqlite_handle)
    action, code = dispatch(state, sqlite_handle)
    assert code == 0
    assert action["step_id"] == "explore"


def test_record_advances_a_sqlite_run(sqlite_handle):
    """record() must write through the store and the next dispatch must see it."""
    _result, rc = record(sqlite_handle, _payload("explore"))
    assert rc == 0

    doc, _, _ = ss.load_doc(sqlite_handle)
    nodes = {n["id"]: n.get("status") for n in doc["workflow_plan"]["main"]["nodes"]}
    assert nodes["explore"] == "completed"
    assert len(doc["step_history"]) == 1
    assert doc["step_history"][0]["outputs"]["reason"] == "explore finished"

    state = load_state(sqlite_handle)
    action, code = dispatch(state, sqlite_handle)
    assert code == 0 and action["step_id"] == "design"


def test_full_run_to_completion_over_sqlite(sqlite_handle):
    for step in ("explore", "design"):
        state = load_state(sqlite_handle)
        action, code = dispatch(state, sqlite_handle)
        assert code == 0 and action["step_id"] == step
        record(sqlite_handle, _payload(step))

    state = load_state(sqlite_handle)
    _action, code = dispatch(state, sqlite_handle)
    assert code == 1, "no ready nodes left -> workflow complete"


def test_usage_lands_in_the_queryable_index(tmp_path, sqlite_handle):
    """Cost per step becomes SQL, not a walk over archived YAML files."""
    import sqlite3

    record(sqlite_handle, _payload("explore"))
    record(sqlite_handle, _payload("design"))

    conn = sqlite3.connect(tmp_path / "state.db")
    rows = conn.execute(
        "SELECT step_id, model, input_tokens, cost_usd FROM step_history "
        "WHERE run_id='orc-901' ORDER BY seq"
    ).fetchall()
    total = conn.execute(
        "SELECT ROUND(SUM(cost_usd), 4) FROM step_history WHERE run_id='orc-901'"
    ).fetchone()[0]
    conn.close()

    assert [r[0] for r in rows] == ["explore", "design"]
    assert rows[0][1] == "claude-sonnet-5"
    assert total == 0.012


def test_a_concurrent_write_is_refused_not_lost(sqlite_handle):
    """Two racing records: one lands, the other is a loud, retryable error.

    On a state.yaml file this is a silent lost update. Here record() returns
    exit 4 with reason `state_write_conflict`.
    """
    from orchestrator_next import record as record_mod

    doc, stale_token, h = ss.load_doc(sqlite_handle)   # reader holds an old token
    record(sqlite_handle, _payload("explore"))          # someone else advances the run

    store, h2 = ss.open_store(sqlite_handle)
    doc["status"] = "clobbered"
    with pytest.raises(ss.StateConflictError):
        store.save(h2, doc, stale_token)

    fresh, _, _ = ss.load_doc(sqlite_handle)
    assert fresh["status"] != "clobbered"
    assert len(fresh["step_history"]) == 1


def test_file_backed_runs_are_completely_unaffected(tmp_path):
    """Back-compat hinge: a bare path still behaves exactly as before."""
    import yaml

    p = tmp_path / "20260818T100000_workflows_feature_state.yaml"
    p.write_text(yaml.safe_dump(json.loads(json.dumps(BASE))), encoding="utf-8")

    state = load_state(str(p))
    action, code = dispatch(state, str(p))
    assert code == 0 and action["step_id"] == "explore"

    record(str(p), _payload("explore"))
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    nodes = {n["id"]: n.get("status") for n in doc["workflow_plan"]["main"]["nodes"]}
    assert nodes["explore"] == "completed"
    assert p.read_text(encoding="utf-8").startswith("change_id: orc-901")
