"""SQLite store for the driver: runs (state), events (inputs), log (trace).

The worker and the CLI are separate processes sharing one file; WAL plus a busy
timeout is all the coordination they need."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

JSON_COLS = ("request", "loop", "waiting", "outcome")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  slug TEXT PRIMARY KEY, request TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|waiting|needs_you|done|failed|stopped
  loop TEXT NOT NULL DEFAULT '{}',        -- attempts, state, history, after, last_ready, last_summary, extra_env, ...
  waiting TEXT,                           -- {kind: gate|question, step_id, attempt|wait, show, question, reason, opened_at}
  current_step TEXT, pid INTEGER,         -- set while a step runs; still set at startup = it was interrupted
  created_at REAL, updated_at REAL, outcome TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL,
  name TEXT NOT NULL,                     -- gate.decided|input.provided|stop
  data TEXT NOT NULL DEFAULT '{}', received_at REAL, consumed_at REAL);
CREATE TABLE IF NOT EXISTS log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, ts REAL, kind TEXT NOT NULL, data TEXT);
CREATE INDEX IF NOT EXISTS log_slug ON log (slug, id);
CREATE INDEX IF NOT EXISTS events_open ON events (slug) WHERE consumed_at IS NULL;
"""


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS + ("data",):
        if d.get(k) is not None:
            d[k] = json.loads(d[k])
    return d


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA busy_timeout=10000")
        self.c.executescript(SCHEMA)

    def tx(self):
        """`with db.tx():` makes the statements inside one atomic commit."""
        return _Tx(self.c)

    def add_run(self, request: dict) -> None:
        now = time.time()
        self.c.execute(
            "INSERT INTO runs (slug, request, created_at, updated_at) VALUES (?,?,?,?)",
            (request["slug"], json.dumps(request), now, now),
        )
        self.log(request["slug"], "submitted", request)

    def run(self, slug: str) -> dict | None:
        return _row(self.c.execute("SELECT * FROM runs WHERE slug=?", (slug,)).fetchone())

    def active_runs(self) -> list[dict]:
        rows = self.c.execute(
            "SELECT * FROM runs WHERE status NOT IN ('done','failed','stopped') ORDER BY created_at, slug"
        )
        return [_row(r) for r in rows]

    def runs(self) -> list[dict]:
        return [_row(r) for r in self.c.execute("SELECT * FROM runs ORDER BY created_at, slug")]

    def update(self, slug: str, **fields) -> None:
        cols = {k: json.dumps(v) if k in JSON_COLS else v for k, v in fields.items()}
        sets = ", ".join(f"{k}=?" for k in cols)
        self.c.execute(f"UPDATE runs SET {sets}, updated_at=? WHERE slug=?", (*cols.values(), time.time(), slug))

    def log(self, slug: str, kind: str, data: dict | None = None) -> None:
        self.c.execute(
            "INSERT INTO log (slug, ts, kind, data) VALUES (?,?,?,?)", (slug, time.time(), kind, json.dumps(data or {}))
        )

    def tail(self, slug: str, n: int = 10) -> list[dict]:
        rows = self.c.execute("SELECT * FROM log WHERE slug=? ORDER BY id DESC LIMIT ?", (slug, n)).fetchall()
        return [_row(r) for r in reversed(rows)]

    def add_event(self, slug: str, name: str, data: dict) -> None:
        self.c.execute(
            "INSERT INTO events (slug, name, data, received_at) VALUES (?,?,?,?)",
            (slug, name, json.dumps(data), time.time()),
        )

    def open_events(self, slug: str | None = None, name: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM events WHERE consumed_at IS NULL", []
        for col, v in (("slug", slug), ("name", name)):
            if v:
                q, args = f"{q} AND {col}=?", [*args, v]
        return [_row(r) for r in self.c.execute(q + " ORDER BY id", args)]

    def consume(self, event_id: int) -> None:
        self.c.execute("UPDATE events SET consumed_at=? WHERE id=?", (time.time(), event_id))


class _Tx:
    def __init__(self, c: sqlite3.Connection):
        self.c = c

    def __enter__(self):
        self.c.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, *_):
        self.c.execute("ROLLBACK" if exc_type else "COMMIT")
