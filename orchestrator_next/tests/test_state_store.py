"""Tests for the pluggable state store.

The contract every backend must satisfy is identical, so the round-trip and
concurrency tests are parametrized across backends. Postgres is skipped unless
ORCHESTRATOR_TEST_PG_DSN is set, and is honestly unproven without it.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import threading

import pytest
import yaml

from orchestrator_next import state_store as ss

DOC = {
    "change_id": "orc-900",
    "slug": "orc-900",
    "ticket_id": "ORC-900",
    "schema": "feature",
    "config_pack": "workflows",
    "status": "active",
    "repo_root": "/repo",
    "phase": "main",
    "workflow_plan": {"main": {"nodes": [
        {"id": "explore", "status": "completed"},
        {"id": "design", "depends_on": ["explore"], "status": "pending"},
    ]}},
    "step_history": [
        {"step_id": "explore", "phase": "main", "status": "completed",
         "agent": "claude", "attempt": 1,
         "started_at": "2026-08-18T10:00:00Z", "ended_at": "2026-08-18T10:04:00Z",
         "usage": {"model": "claude-sonnet-5", "input_tokens": 1200,
                   "output_tokens": 340, "cache_read_input_tokens": 900,
                   "cache_creation_input_tokens": 100, "cost_usd": 0.021,
                   "duration_ms": 240000}},
    ],
    "retries": {},
}


# --------------------------------------------------------------- handle parsing
@pytest.mark.parametrize("raw,scheme,run_id", [
    ("/repo/.orchestrator/orc-1/x_state.yaml", "file", ""),
    ("file:///repo/x_state.yaml", "file", ""),
    ("sqlite:///repo/state.db#orc-123", "sqlite", "orc-123"),
    ("postgresql://u@h:5432/orch#orc-9", "postgresql", "orc-9"),
])
def test_parse_handle(raw, scheme, run_id):
    h = ss.parse_handle(raw)
    assert h.scheme == scheme
    assert h.run_id == run_id


def test_bare_path_is_a_file_handle_not_a_url():
    """A plain path must never be mistaken for a URL — this is the back-compat hinge."""
    h = ss.parse_handle("/a/b/c_state.yaml")
    assert h.is_file and h.location == "/a/b/c_state.yaml"


def test_unknown_scheme_is_a_loud_error():
    with pytest.raises(ValueError, match="unsupported state URL scheme"):
        ss.parse_handle("mysql://host/db#run")


def test_sql_handle_without_run_id_is_rejected():
    store, h = ss.open_store("sqlite:///tmp/x.db")
    with pytest.raises(ValueError, match="append '#<run-id>'"):
        store.load(h)


# ------------------------------------------------------------------- fixtures
@pytest.fixture(params=["file", "sqlite"])
def handle(request, tmp_path):
    if request.param == "file":
        return str(tmp_path / "20260818T100000_workflows_feature_state.yaml")
    return f"sqlite:///{tmp_path}/state.db#orc-900"


# ------------------------------------------------------------ round-trip contract
def test_create_load_save_round_trip(handle):
    store, h = ss.open_store(handle)
    token = store.create(h, DOC)
    doc, token2 = store.load(h)
    assert doc == DOC
    assert doc["workflow_plan"]["main"]["nodes"][1]["id"] == "design"

    doc["status"] = "blocked"
    store.save(h, doc, token2)
    again, _ = store.load(h)
    assert again["status"] == "blocked"


def test_missing_run_raises_not_found(handle):
    store, h = ss.open_store(handle)
    with pytest.raises(ss.StateNotFoundError):
        store.load(h)


def test_exists(handle):
    store, h = ss.open_store(handle)
    assert store.exists(h) is False
    store.create(h, DOC)
    assert store.exists(h) is True


# -------------------------------------------------- optimistic concurrency
def test_stale_token_is_rejected(handle):
    """The whole point: a lost update becomes a loud error, not silence."""
    store, h = ss.open_store(handle)
    store.create(h, DOC)

    doc_a, token_a = store.load(h)      # reader A
    doc_b, token_b = store.load(h)      # reader B, same version

    doc_a["status"] = "written-by-a"
    store.save(h, doc_a, token_a)       # A wins

    doc_b["status"] = "written-by-b"
    with pytest.raises(ss.StateConflictError):
        store.save(h, doc_b, token_b)   # B must not silently clobber A

    final, _ = store.load(h)
    assert final["status"] == "written-by-a"


def test_token_advances_on_every_save(handle):
    store, h = ss.open_store(handle)
    t0 = store.create(h, DOC)
    doc, t1 = store.load(h)
    doc["status"] = "x"
    t2 = store.save(h, doc, t1)
    assert t2 != t1 and t1 is not None and t0 is not None


def test_concurrent_writers_exactly_one_wins(tmp_path):
    """Eight threads, one run. Under state.yaml today this silently loses writes."""
    url = f"sqlite:///{tmp_path}/state.db#orc-900"
    store, h = ss.open_store(url)
    store.create(h, DOC)
    doc, token = store.load(h)

    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def writer(i: int) -> None:
        d = json.loads(json.dumps(doc))
        d["status"] = f"writer-{i}"
        barrier.wait()
        try:
            store.save(h, d, token)
            with lock:
                outcomes.append("won")
        except ss.StateConflictError:
            with lock:
                outcomes.append("conflict")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(writer, range(8)))

    assert outcomes.count("won") == 1, outcomes
    assert outcomes.count("conflict") == 7, outcomes


def test_serial_read_modify_write_all_succeed(tmp_path):
    """Reload-then-write always makes progress — retry is a valid strategy."""
    url = f"sqlite:///{tmp_path}/state.db#orc-900"
    store, h = ss.open_store(url)
    store.create(h, DOC)
    for i in range(20):
        doc, token = store.load(h)
        doc.setdefault("step_history", []).append(
            {"step_id": f"s{i}", "phase": "main", "status": "completed",
             "agent": "claude", "attempt": 1, "usage": {"model": "m"}})
        store.save(h, doc, token)
    doc, _ = store.load(h)
    assert len(doc["step_history"]) == 21


# --------------------------------------------------------- file back-compat
def test_file_store_writes_plain_readable_yaml(tmp_path):
    """The 'debuggable with cat' property must survive for the default backend."""
    p = tmp_path / "s_state.yaml"
    store, h = ss.open_store(str(p))
    store.create(h, DOC)
    text = p.read_text(encoding="utf-8")
    assert "change_id: orc-900" in text
    assert yaml.safe_load(text)["schema"] == "feature"


def test_file_store_restores_previous_bytes_on_bad_write(tmp_path, monkeypatch):
    p = tmp_path / "s_state.yaml"
    store, h = ss.open_store(str(p))
    store.create(h, DOC)
    good = p.read_bytes()
    doc, token = store.load(h)

    def boom(*a, **k):
        raise yaml.YAMLError("post-write parse failed")

    monkeypatch.setattr(yaml, "safe_load", boom)
    with pytest.raises(yaml.YAMLError):
        store.save(h, doc, token)
    monkeypatch.undo()
    assert p.read_bytes() == good, "the previous state must be restored intact"


def test_file_store_detects_an_external_edit(tmp_path):
    p = tmp_path / "s_state.yaml"
    store, h = ss.open_store(str(p))
    store.create(h, DOC)
    doc, token = store.load(h)
    p.write_text("change_id: hand-edited\n", encoding="utf-8")  # someone else wrote
    with pytest.raises(ss.StateConflictError):
        store.save(h, doc, token)


# ----------------------------------------------------- derived history index
def test_step_history_is_queryable_in_sqlite(tmp_path):
    """This is what replaces globbing archived state files in `report --all`."""
    db = tmp_path / "state.db"
    for n in range(3):
        store, h = ss.open_store(f"sqlite:///{db}#orc-{n}")
        d = json.loads(json.dumps(DOC))
        d["slug"] = f"orc-{n}"
        d["step_history"][0]["usage"]["cost_usd"] = 0.10 * (n + 1)
        store.create(h, d)

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT step_id, COUNT(*), ROUND(AVG(cost_usd), 4), SUM(input_tokens) "
        "FROM step_history GROUP BY step_id"
    ).fetchall()
    conn.close()
    assert rows == [("explore", 3, 0.2, 3600)]


def test_history_index_is_rebuilt_not_appended(tmp_path):
    """The index is derived. A save must not duplicate rows."""
    db = tmp_path / "state.db"
    store, h = ss.open_store(f"sqlite:///{db}#orc-900")
    store.create(h, DOC)
    for _ in range(3):
        doc, token = store.load(h)
        store.save(h, doc, token)
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM step_history WHERE run_id='orc-900'").fetchone()[0]
    conn.close()
    assert n == 1


def test_doc_is_the_source_of_truth_not_the_columns(tmp_path):
    """Extracted columns are an index. Round-tripping must not lose unknown keys."""
    db = tmp_path / "state.db"
    store, h = ss.open_store(f"sqlite:///{db}#orc-900")
    d = json.loads(json.dumps(DOC))
    d["some_future_key"] = {"nested": [1, 2, 3]}
    store.create(h, d)
    back, _ = store.load(h)
    assert back["some_future_key"] == {"nested": [1, 2, 3]}


# ---------------------------------------------------------------- projection
def test_project_yaml_materializes_a_readable_file_for_pack_scripts(tmp_path):
    """Eight pack scripts read state.yaml by path; a SQL store has no such file."""
    store, h = ss.open_store(f"sqlite:///{tmp_path}/state.db#orc-900")
    store.create(h, DOC)
    out = ss.project_yaml(f"sqlite:///{tmp_path}/state.db#orc-900", tmp_path / "proj" / "state.yaml")
    assert out.is_file()
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded == DOC


# ----------------------------------------------------------------- migration
def test_import_yaml_moves_an_existing_run_into_a_store(tmp_path):
    src = tmp_path / "20260818T100000_workflows_feature_state.yaml"
    src.write_text(yaml.safe_dump(DOC), encoding="utf-8")
    url = f"sqlite:///{tmp_path}/state.db"
    handle = ss.import_yaml(src, url)
    assert handle.endswith("#20260818T100000_workflows_feature")
    doc, _, _ = ss.load_doc(handle)
    assert doc == DOC


def test_import_yaml_refuses_to_overwrite(tmp_path):
    src = tmp_path / "a_state.yaml"
    src.write_text(yaml.safe_dump(DOC), encoding="utf-8")
    url = f"sqlite:///{tmp_path}/state.db"
    ss.import_yaml(src, url, run_id="orc-900")
    with pytest.raises(FileExistsError):
        ss.import_yaml(src, url, run_id="orc-900")


def test_list_runs(tmp_path):
    url = f"sqlite:///{tmp_path}/state.db"
    for n in range(3):
        store, h = ss.open_store(f"{url}#orc-{n}")
        d = json.loads(json.dumps(DOC)); d["slug"] = f"orc-{n}"
        store.create(h, d)
    store, h = ss.open_store(f"{url}#ignored")
    runs = store.list_runs(h)
    assert [r["slug"] for r in runs] == ["orc-0", "orc-1", "orc-2"]
    assert all(r["schema"] == "feature" for r in runs)


# ------------------------------------------------------------------ postgres
PG_DSN = os.environ.get("ORCHESTRATOR_TEST_PG_DSN")


@pytest.mark.skipif(not PG_DSN, reason="set ORCHESTRATOR_TEST_PG_DSN to run")
def test_postgres_round_trip_and_conflict():
    url = f"{PG_DSN}#orc-900-test"
    store, h = ss.open_store(url)
    try:
        store.create(h, DOC)
        doc_a, tok_a = store.load(h)
        doc_b, tok_b = store.load(h)
        doc_a["status"] = "a"
        store.save(h, doc_a, tok_a)
        doc_b["status"] = "b"
        with pytest.raises(ss.StateConflictError):
            store.save(h, doc_b, tok_b)
    finally:
        import psycopg  # type: ignore
        with psycopg.connect(h.location) as c:
            c.execute("DELETE FROM runs WHERE run_id = %s", ("orc-900-test",))
            c.execute("DELETE FROM step_history WHERE run_id = %s", ("orc-900-test",))
