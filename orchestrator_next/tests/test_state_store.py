"""Tests for the state store.

State is one YAML document per run. The properties that matter are the
compare-and-swap (a lost update must be a loud error, never silence) and the
restore-on-bad-write guarantee — both are data-loss protection.
"""
from __future__ import annotations

import concurrent.futures
import json
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
        {"id": "design", "status": "pending"},
    ]}},
    "step_history": [
        {"step_id": "explore", "phase": "main", "status": "completed",
         "attempt": 1},
    ],
}


# ------------------------------------------------------------ handle parsing
@pytest.mark.parametrize("raw", [
    "/repo/.orchestrator/orc-1/x_state.yaml",
    "file:///repo/x_state.yaml",
])
def test_parse_handle_is_always_a_file(raw):
    h = ss.parse_handle(raw)
    assert h.scheme == "file"
    assert h.run_id == ""


def test_bare_path_is_a_file_handle_not_a_url():
    h = ss.parse_handle("/a/b/c_state.yaml")
    assert h.is_file and h.location == "/a/b/c_state.yaml"


def test_a_remote_url_is_a_loud_error():
    """Remote backends are gone — a driver names a directory, not a server."""
    with pytest.raises(ValueError, match="unsupported state URL scheme"):
        ss.parse_handle("postgresql://u@h:5432/orch#orc-9")
    with pytest.raises(ValueError, match="unsupported state URL scheme"):
        ss.parse_handle("sqlite:///repo/state.db#orc-123")


@pytest.fixture
def handle(tmp_path):
    return str(tmp_path / "20260818T100000_workflows_feature_state.yaml")


# ------------------------------------------------------------ round-trip
def test_create_load_save_round_trip(handle):
    store, h = ss.open_store(handle)
    store.create(h, DOC)
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


def test_concurrent_writers_exactly_one_wins(handle):
    """Eight threads, one run: seven must be refused, not silently dropped."""
    store, h = ss.open_store(handle)
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


def test_serial_read_modify_write_all_succeed(handle):
    """Reload-then-write always makes progress — retry is a valid strategy."""
    store, h = ss.open_store(handle)
    store.create(h, DOC)
    for i in range(20):
        doc, token = store.load(h)
        doc.setdefault("step_history", []).append(
            {"step_id": f"s{i}", "phase": "main", "status": "completed",
             "attempt": 1})
        store.save(h, doc, token)
    doc, _ = store.load(h)
    assert len(doc["step_history"]) == 21


# --------------------------------------------------------- file semantics
def test_file_store_writes_plain_readable_yaml(tmp_path):
    """The 'debuggable with cat' property is the reason state is a file."""
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


def test_list_runs_reads_the_state_directory(tmp_path):
    for n in range(3):
        store, h = ss.open_store(str(tmp_path / f"orc-{n}.yaml"))
        d = json.loads(json.dumps(DOC))
        d["slug"] = f"orc-{n}"
        store.create(h, d)
    store, h = ss.open_store(str(tmp_path / "orc-0.yaml"))
    runs = store.list_runs(h)
    assert [r["slug"] for r in runs] == ["orc-0", "orc-1", "orc-2"]
    assert all(r["schema"] == "feature" for r in runs)
