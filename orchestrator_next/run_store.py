"""RunStore — durable backing for session/run state.

One key per run id, holding the JSON/YAML payload the caller persists. A run's
state lives in the RunStore for as long as it's alive; on completion it's
archived (renamed, TTL removed), never deleted — the archived key is the
machine-readable audit trail. Nothing is ever written into the repo.

Backend: whatever database workflow state lives in — the shared orchestrator
SQLite db (`~/.orchestrator/orchestrator.db`, see state_store.default_db_path)
by default, or the remote db named by ORCHESTRATOR_STATE_URL. One db for the
whole orchestrator: run blobs ride the same URL as workflow state.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Protocol


class RunStore(Protocol):
    def load(self, run_id: str, *, archived: bool = False) -> str | None: ...
    def save(self, run_id: str, text: str) -> None: ...
    def delete(self, run_id: str) -> None: ...
    def list_ids(self, *, archived: bool = False) -> list[str]: ...
    def lock(self, run_id: str) -> bool: ...
    def refresh_lock(self, run_id: str) -> None: ...
    def unlock(self, run_id: str) -> None: ...
    def archive(self, run_id: str) -> None: ...


# ponytail: fixed lock TTL, refreshed once per driving iteration (a step
# taking longer than this between iterations loses the lock) — raise this or
# refresh more often if a single step ever runs past ~15 minutes.
LOCK_TTL = 900


class _SqlRunStore:
    """Shared SQL for every RunStore backend; subclasses supply the connection.

    Tables are `run_blobs` / `run_locks` so they coexist with state_store's
    `runs` / `step_history` schema in the same database. Locks carry TTL
    semantics via an ``expires_at`` column, so a crashed driver's lock still
    ages out after LOCK_TTL.
    """

    ph = "?"
    # REAL is fine for sqlite; postgres overrides (its REAL is a 4-byte float,
    # which cannot hold epoch seconds exactly).
    expires_type = "REAL"

    def _conn(self):  # pragma: no cover — overridden
        raise NotImplementedError

    def _q(self, sql: str) -> str:
        return sql if self.ph == "?" else sql.replace("?", "%s")

    def _ensure_schema(self, conn) -> None:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS run_blobs ("
            " run_id TEXT PRIMARY KEY, text TEXT NOT NULL,"
            " archived INTEGER NOT NULL DEFAULT 0)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS run_locks ("
            f" run_id TEXT PRIMARY KEY, expires_at {self.expires_type} NOT NULL)"
        )

    def load(self, run_id: str, *, archived: bool = False) -> str | None:
        with closing(self._conn()) as conn:
            cur = conn.cursor()
            cur.execute(
                self._q("SELECT text FROM run_blobs WHERE run_id = ? AND archived = ?"),
                (run_id, int(archived)),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def save(self, run_id: str, text: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.cursor().execute(
                self._q(
                    "INSERT INTO run_blobs (run_id, text, archived) VALUES (?, ?, 0) "
                    "ON CONFLICT(run_id) DO UPDATE SET text = excluded.text, archived = 0"
                ),
                (run_id, text),
            )

    def delete(self, run_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.cursor().execute(
                self._q("DELETE FROM run_blobs WHERE run_id = ? AND archived = 0"),
                (run_id,),
            )

    def list_ids(self, *, archived: bool = False) -> list[str]:
        with closing(self._conn()) as conn:
            cur = conn.cursor()
            cur.execute(
                self._q("SELECT run_id FROM run_blobs WHERE archived = ?"),
                (int(archived),),
            )
            rows = cur.fetchall()
        return [r[0] for r in rows]

    def lock(self, run_id: str) -> bool:
        now = time.time()
        with closing(self._conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute(
                self._q(
                    "INSERT INTO run_locks (run_id, expires_at) VALUES (?, ?) "
                    "ON CONFLICT(run_id) DO UPDATE SET expires_at = excluded.expires_at "
                    "WHERE run_locks.expires_at < ?"
                ),
                (run_id, now + LOCK_TTL, now),
            )
            return cur.rowcount > 0

    def refresh_lock(self, run_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.cursor().execute(
                self._q(
                    "INSERT INTO run_locks (run_id, expires_at) VALUES (?, ?) "
                    "ON CONFLICT(run_id) DO UPDATE SET expires_at = excluded.expires_at"
                ),
                (run_id, time.time() + LOCK_TTL),
            )

    def unlock(self, run_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.cursor().execute(
                self._q("DELETE FROM run_locks WHERE run_id = ?"), (run_id,)
            )

    def archive(self, run_id: str) -> None:
        """Flip the live row to archived; no-op when no live row exists."""
        with closing(self._conn()) as conn, conn:
            conn.cursor().execute(
                self._q("UPDATE run_blobs SET archived = 1 WHERE run_id = ? AND archived = 0"),
                (run_id,),
            )


class SqliteRunStore(_SqlRunStore):
    """The RunStore on the shared local SQLite file (the default)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        from orchestrator_next.state_store import default_db_path

        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as conn, conn:
            self._ensure_schema(conn)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)


class PostgresRunStore(_SqlRunStore):
    """The RunStore on the same remote postgres db workflow state uses."""

    ph = "%s"
    expires_type = "DOUBLE PRECISION"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._schema_ready = False

    def _conn(self):
        try:
            import psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover — env-dependent
            raise RuntimeError(
                "postgresql:// state URLs need psycopg — "
                "install it with `uv add psycopg[binary]`"
            ) from exc
        conn = psycopg.connect(self.dsn)
        if not self._schema_ready:
            with conn:
                self._ensure_schema(conn)
            self._schema_ready = True
        return conn


def open_store() -> RunStore:
    """The RunStore in the same db as workflow state.

    ORCHESTRATOR_STATE_URL routes both: a sqlite:// URL puts blobs in that
    file, postgresql:// in that server. Unset (or the file backend, which has
    no db of its own) falls back to the shared local SQLite db.
    """
    from orchestrator_next import state_store

    url = os.environ.get(state_store.ENV_STATE_URL, "").strip()
    if url:
        handle = state_store.parse_handle(url)
        if handle.scheme == "postgresql":
            return PostgresRunStore(handle.location)
        if handle.scheme == "sqlite":
            return SqliteRunStore(handle.location)
    return SqliteRunStore()


def _state_root() -> Path:
    """Machine-local materialization dir — never inside a repo, never gitignored
    (nothing to ignore: it isn't under any repo)."""
    return Path(os.environ.get("ORCHESTRATOR_HOME_DIR", "~/.orchestrator")).expanduser() / "state"


def rebind_repo_root(text: str, repo_root: str) -> str:
    """Rewrite ``repo_root`` in state YAML text to this machine's path.

    ``repo_root`` was stamped by whichever machine ran this state last; on
    resume it must point at this machine's path instead. Non-dict/unparseable
    YAML is returned unchanged rather than raised on.
    """
    import yaml

    if not repo_root:
        return text
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return text
    if not isinstance(raw, dict):
        return text
    raw["repo_root"] = repo_root
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def materialize(store: "RunStore", run_id: str, *, repo_root: str = "") -> Path:
    """Load ``run_id``'s state text and write it to a stable per-run path.

    Stable (not a per-call tempfile) so ``--seed-only`` followed by a separate
    ``orchestrator next``/``done`` invocation still resolves the same file —
    both look up the same run_id and land on the same path. When ``repo_root``
    is given, rebind the materialized state's ``repo_root`` to it (a resume on
    a different machine/checkout must not keep the previous machine's path).
    """
    text = store.load(run_id)
    if text is None:
        raise FileNotFoundError(f"no state for run_id={run_id}")
    text = rebind_repo_root(text, repo_root)
    path = _state_root() / f"{run_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def persist(store: "RunStore", run_id: str, state_path: Path | str) -> None:
    """Save the materialized file's current contents back to the store."""
    store.save(run_id, Path(state_path).read_text(encoding="utf-8"))
