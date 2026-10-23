"""The store: it survives the server dropping its connection (a hosted Postgres does that when idle)."""

from __future__ import annotations

import psycopg
import pytest

from orchestrator_driver.db import DB


def kill_backend(db: DB, url: str) -> None:
    with psycopg.connect(url, autocommit=True) as other:
        other.execute("SELECT pg_terminate_backend(%s)", (db.c.info.backend_pid,))


def test_reconnects_after_the_connection_is_killed(database_url):
    db = DB(database_url)
    db.add_run({"slug": "r1"})
    kill_backend(db, database_url)
    assert db.run("r1")["slug"] == "r1"  # the first call after the drop reconnects and retries
    kill_backend(db, database_url)
    db.add_run({"slug": "r2"})
    assert [r["slug"] for r in db.runs()] == ["r1", "r2"]
    db.close()


def test_a_transaction_is_not_retried(database_url):
    db = DB(database_url)
    db.add_run({"slug": "r1"})
    with pytest.raises(psycopg.OperationalError), db.tx():
        db.update("r1", status="running")
        kill_backend(db, database_url)
        db.update("r1", status="done")
    assert db.run("r1")["status"] == "queued"  # rolled back with the connection
    db.close()
