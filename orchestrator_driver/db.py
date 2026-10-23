"""Postgres store for the driver: runs (state), events (inputs), log (trace).

The worker and the CLI are separate processes, possibly on separate machines, sharing one database.
Autocommit outside `tx()`; JSON stays in TEXT columns."""

from __future__ import annotations

import json
import time

import psycopg
from psycopg.rows import dict_row

JSON_COLS = ("request", "loop", "waiting", "outcome")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  slug TEXT PRIMARY KEY, request TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|waiting|needs_you|done|failed|stopped
  loop TEXT NOT NULL DEFAULT '{}',        -- attempts, state, history, after, last_ready, last_summary, extra_env, ...
  waiting TEXT,                           -- {kind: gate|question, step_id, attempt|wait, show, question, reason, opened_at}
  current_step TEXT, pid INTEGER,        -- set while a step runs; still set at startup = it was interrupted
  created_at DOUBLE PRECISION, updated_at DOUBLE PRECISION, outcome TEXT,
  started_at DOUBLE PRECISION);          -- when the step process (pid, a process group leader) started
CREATE TABLE IF NOT EXISTS events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, slug TEXT NOT NULL,
  name TEXT NOT NULL,                     -- gate.decided|input.provided|stop
  data TEXT NOT NULL DEFAULT '{}', received_at DOUBLE PRECISION, consumed_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, slug TEXT NOT NULL, ts DOUBLE PRECISION, kind TEXT NOT NULL, data TEXT);
CREATE INDEX IF NOT EXISTS log_slug ON log (slug, id);
CREATE INDEX IF NOT EXISTS events_open ON events (slug) WHERE consumed_at IS NULL;
"""


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    for k in JSON_COLS + ("data",):
        if r.get(k) is not None:
            r[k] = json.loads(r[k])
    return r


class DB:
    IntegrityError = psycopg.errors.UniqueViolation  # add_run on a slug that exists

    def __init__(self, url: str):
        self.url, self.in_tx = url, False
        self.c = self._connect()
        with self.c.transaction():
            self.c.execute("SELECT pg_advisory_xact_lock(7243901)")  # two processes creating the tables at once
            self.c.execute(SCHEMA)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)

    def _x(self, sql: str, args=None) -> psycopg.Cursor:
        """Every statement goes through here. A hosted Postgres drops idle connections: outside a transaction,
        reconnect once and retry once. Inside one the error propagates and the transaction is gone."""
        try:
            return self.c.execute(sql, args)
        except psycopg.OperationalError:
            if self.in_tx or not (self.c.closed or self.c.broken):
                raise
            self.c = self._connect()
            return self.c.execute(sql, args)

    def close(self) -> None:
        self.c.close()

    def tx(self):
        """`with db.tx():` makes the statements inside one atomic commit."""
        return _Tx(self)

    def add_run(self, request: dict) -> None:
        now = time.time()
        self._x(
            "INSERT INTO runs (slug, request, created_at, updated_at) VALUES (%s,%s,%s,%s)",
            (request["slug"], json.dumps(request), now, now),
        )
        self.log(request["slug"], "submitted", request)

    def run(self, slug: str) -> dict | None:
        return _row(self._x("SELECT * FROM runs WHERE slug=%s", (slug,)).fetchone())

    def active_runs(self) -> list[dict]:
        rows = self._x("SELECT * FROM runs WHERE status NOT IN ('done','failed','stopped') ORDER BY created_at, slug")
        return [_row(r) for r in rows]

    def runs(self) -> list[dict]:
        return [_row(r) for r in self._x("SELECT * FROM runs ORDER BY created_at, slug")]

    def update(self, slug: str, **fields) -> None:
        cols = {k: json.dumps(v) if k in JSON_COLS else v for k, v in fields.items()}
        sets = ", ".join(f"{k}=%s" for k in cols)
        self._x(f"UPDATE runs SET {sets}, updated_at=%s WHERE slug=%s", (*cols.values(), time.time(), slug))

    def log(self, slug: str, kind: str, data: dict | None = None) -> None:
        self._x(
            "INSERT INTO log (slug, ts, kind, data) VALUES (%s,%s,%s,%s)",
            (slug, time.time(), kind, json.dumps(data or {})),
        )

    def tail(self, slug: str, n: int = 10) -> list[dict]:
        rows = self._x("SELECT * FROM log WHERE slug=%s ORDER BY id DESC LIMIT %s", (slug, n)).fetchall()
        return [_row(r) for r in reversed(rows)]

    def add_event(self, slug: str, name: str, data: dict) -> None:
        self._x(
            "INSERT INTO events (slug, name, data, received_at) VALUES (%s,%s,%s,%s)",
            (slug, name, json.dumps(data), time.time()),
        )

    def open_events(self, slug: str | None = None, name: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM events WHERE consumed_at IS NULL", []
        for col, v in (("slug", slug), ("name", name)):
            if v:
                q, args = f"{q} AND {col}=%s", [*args, v]
        return [_row(r) for r in self._x(q + " ORDER BY id", args)]

    def consume(self, event_id: int) -> None:
        self._x("UPDATE events SET consumed_at=%s WHERE id=%s", (time.time(), event_id))


class _Tx:
    """BEGIN/COMMIT on the autocommit connection. Only the worker reads then writes inside one, and
    there is one worker, so no row locks are needed: the transaction is for atomicity."""

    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db._x("BEGIN")
        self.db.in_tx = True

    def __exit__(self, exc_type, *_):
        try:
            if not (exc_type and (self.db.c.closed or self.db.c.broken)):  # a dead connection already rolled back
                self.db._x("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.in_tx = False
