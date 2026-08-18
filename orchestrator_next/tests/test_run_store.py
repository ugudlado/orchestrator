"""RunStore: one local SQLite backend."""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next.run_store import (
    SqliteRunStore,
    materialize,
    open_store,
    persist,
)


def test_open_store_uses_local_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path))
    store = open_store()
    assert isinstance(store, SqliteRunStore)
    assert str(store.db_path).startswith(str(tmp_path))


def test_sqlite_store_round_trip_and_archive(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    assert store.load("run-1") is None

    store.save("run-1", '{"a": 1}')
    assert store.load("run-1") == '{"a": 1}'
    assert "run-1" in store.list_ids()

    store.save("run-1", '{"a": 2}')
    assert store.load("run-1") == '{"a": 2}'

    store.archive("run-1")
    assert store.load("run-1") is None
    assert "run-1" not in store.list_ids()
    assert store.load("run-1", archived=True) == '{"a": 2}'
    assert "run-1" in store.list_ids(archived=True)

    store.save("run-2", '{"b": 1}')
    store.delete("run-2")
    assert store.load("run-2") is None
    assert "run-2" not in store.list_ids()

    store.delete("run-3")  # deleting a missing run is a no-op
    store.archive("run-3")  # archiving a missing run is a no-op


def test_sqlite_lock_is_exclusive_until_unlocked_or_expired(tmp_path, monkeypatch):
    import orchestrator_next.run_store as rs

    store = SqliteRunStore(tmp_path / "runs.db")
    assert store.lock("run-1") is True
    assert store.lock("run-1") is False  # second claimant loses

    store.unlock("run-1")
    assert store.lock("run-1") is True  # freed lock is claimable again

    # An expired lock ages out (TTL semantics).
    future = rs.time.time() + rs.LOCK_TTL * 3
    monkeypatch.setattr(rs.time, "time", lambda: future)
    assert store.lock("run-1") is True


def test_archive_is_idempotent_when_already_archived(tmp_path):
    """A re-entrant archive call (double-call, crash-and-retry) is a no-op."""
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save("run-1", '{"a": 1}')

    store.archive("run-1")
    store.archive("run-1")  # must not raise

    assert "run-1" in store.list_ids(archived=True)


def test_lock_refresh_extends_expiry(tmp_path, monkeypatch):
    import orchestrator_next.run_store as rs

    store = SqliteRunStore(tmp_path / "runs.db")
    assert store.lock("run-1")

    # Move time just short of expiry, refresh, then past the original expiry:
    # the refreshed lock must still hold.
    base = rs.time.time()
    monkeypatch.setattr(rs.time, "time", lambda: base + rs.LOCK_TTL - 1)
    store.refresh_lock("run-1")
    monkeypatch.setattr(rs.time, "time", lambda: base + rs.LOCK_TTL + 1)
    assert store.lock("run-1") is False  # refreshed lock still held


def test_materialize_writes_stable_path_and_persist_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path))
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save("run-1", yaml.safe_dump({"change_id": "run-1", "repo_root": "/old/machine"}))

    path = materialize(store, "run-1", repo_root="/new/machine")
    assert path == tmp_path / "state" / "run-1.yaml"
    raw = yaml.safe_load(path.read_text())
    assert raw["repo_root"] == "/new/machine"

    # Same run_id materializes to the same path (stable across --seed-only / next / done).
    path2 = materialize(store, "run-1", repo_root="/new/machine")
    assert path2 == path

    raw["status"] = "completed"
    path.write_text(yaml.safe_dump(raw))
    persist(store, "run-1", path)
    assert "completed" in store.load("run-1")


def test_materialize_raises_for_unknown_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path))
    store = SqliteRunStore(tmp_path / "runs.db")
    with pytest.raises(FileNotFoundError):
        materialize(store, "does-not-exist")


def test_run_store_and_state_store_share_one_db(tmp_path, monkeypatch):
    """One db file for everything: RunStore tables coexist with StateStore's."""
    from orchestrator_next import state_store as ss

    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path))
    db = ss.default_db_path()
    assert db == tmp_path / "orchestrator.db"

    run_store = SqliteRunStore()
    assert run_store.db_path == db
    run_store.save("blob-1", '{"a": 1}')

    store, handle = ss.open_store(f"sqlite:///{str(db).lstrip('/')}#orc-1")
    store.create(handle, {"change_id": "orc-1", "status": "active"})

    assert run_store.load("blob-1") == '{"a": 1}'
    doc, _tok = store.load(handle)
    assert doc["change_id"] == "orc-1"
