"""Pluggable state storage — YAML file (default), SQLite, or Postgres.

Why
---
`state.yaml` is the entire memory of a run, and every good property the engine
has falls out of that. Two things it cannot do:

  * **Concurrency.** `safe_write_yaml` truncates and rewrites the whole file with
    no lock. That is safe today only because `run_loop` is strictly sequential.
    The moment `next_ready_node()` becomes `ready_nodes()` and two steps run at
    once, two writers race and one update is silently lost.
  * **Cross-run queries.** `report --all` globs archived files and re-parses
    every one. There is no way to ask "what did code-review cost across the last
    50 feature runs" without reading 50 YAML documents.

This module puts a store behind the two primitives the engine already funnels
everything through — `load` and `save` — so the rest of the engine does not
change shape.

The handle
----------
Wherever the engine passes `state_yaml_path` it may now pass a URL. Both work:

    /repo/.orchestrator/orc-1/2026..._state.yaml   -> FileStore   (default)
    file:///repo/.../state.yaml                    -> FileStore
    sqlite:///repo/.orchestrator/state.db#orc-123  -> SqliteStore, run orc-123
    postgresql://user@host/orch#orc-123            -> PostgresStore, run orc-123

The fragment is the run id — the equivalent of which file you opened.
`ORCHESTRATOR_STATE_URL` sets a default base for new runs.

Optimistic concurrency
----------------------
`load()` returns `(doc, token)`; `save()` requires that token back and raises
`StateConflictError` if the row moved underneath you. For `FileStore` the token
is the pre-write bytes — which is exactly what `safe_write_yaml` already carried
for rollback, so file semantics are byte-identical to today. For SQL stores it
is a version integer and the update is `WHERE version = ?`.

That single change is what makes parallel dispatch safe later: a lost update
becomes a loud retry instead of a silently dropped step outcome.

Source of truth
---------------
SQL backends store the **whole document** in one JSON column. Nothing in the
engine has to learn a relational model, and no state semantics can drift during
the migration. A derived `step_history` table is rebuilt on every save purely so
cross-run reporting is a query rather than a directory walk — it is an index,
never the authority.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

# An opaque compare-and-swap token. bytes for files, int for SQL rows.
Token = Any

ENV_STATE_URL = "ORCHESTRATOR_STATE_URL"
_SQLITE_SCHEMES = ("sqlite", "sqlite3")
_PG_SCHEMES = ("postgresql", "postgres")


class StateConflictError(RuntimeError):
    """The run changed since it was loaded — the caller must reload and retry."""


class StateNotFoundError(FileNotFoundError):
    """No such run in the store."""


# ---------------------------------------------------------------------------
# Handle parsing
# ---------------------------------------------------------------------------
class StateHandle:
    """Where a run lives. Either a filesystem path or (store URL + run id)."""

    __slots__ = ("scheme", "location", "run_id", "raw")

    def __init__(self, scheme: str, location: str, run_id: str, raw: str) -> None:
        self.scheme = scheme
        self.location = location
        self.run_id = run_id
        self.raw = raw

    @property
    def is_file(self) -> bool:
        return self.scheme == "file"

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"StateHandle({self.scheme}:{self.location} #{self.run_id})"


def parse_handle(handle: str | os.PathLike[str] | StateHandle) -> StateHandle:
    """Parse a path or URL into a StateHandle. A bare path is a file handle."""
    if isinstance(handle, StateHandle):
        return handle
    raw = str(handle)
    parsed = urllib.parse.urlsplit(raw)
    scheme = parsed.scheme.lower()

    # A Windows drive letter ("C:\...") parses as scheme "c" — treat any
    # single-character scheme as a path, not a URL.
    if not scheme or len(scheme) == 1:
        return StateHandle("file", str(Path(raw)), "", raw)

    if scheme == "file":
        return StateHandle("file", urllib.parse.unquote(parsed.path), "", raw)

    if scheme in _SQLITE_SCHEMES:
        # sqlite:///abs/path.db#run-id  — netloc empty, path absolute
        location = urllib.parse.unquote(parsed.path)
        return StateHandle("sqlite", location, parsed.fragment, raw)

    if scheme in _PG_SCHEMES:
        # Rebuild the DSN without the fragment; psycopg does not want it.
        dsn = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                       parsed.query, ""))
        return StateHandle("postgresql", dsn, parsed.fragment, raw)

    raise ValueError(
        f"unsupported state URL scheme {scheme!r} in {raw!r} — "
        f"expected a path, file://, sqlite:// or postgresql://"
    )


ENV_STATE_BACKEND = "ORCHESTRATOR_STATE_BACKEND"
DEFAULT_BACKEND = "sqlite"


def default_db_path() -> Path:
    """The ONE SQLite file every orchestrator store shares.

    Workflow state (this module) and run/session blobs (run_store) live as
    separate tables in the same `~/.orchestrator/orchestrator.db` — one db to
    manage, back up, or point somewhere else. A remote/central db is the
    ORCHESTRATOR_STATE_URL override, not a second file.
    """
    home = Path(os.environ.get("ORCHESTRATOR_HOME_DIR", "~/.orchestrator")).expanduser()
    return home / "orchestrator.db"


def default_backend() -> str:
    """`sqlite` unless overridden. `file` restores one-YAML-per-run."""
    return (os.environ.get(ENV_STATE_BACKEND) or DEFAULT_BACKEND).strip().lower()


def default_state_url(repo_root: str = "") -> str:
    """The store new runs are created in.

    Precedence: ORCHESTRATOR_STATE_URL, then the backend default: the shared
    machine-global SQLite db (see default_db_path). Run ids carry
    slug + timestamp + schema, so runs from every repo coexist in one table.
    """
    explicit = os.environ.get(ENV_STATE_URL, "").strip()
    if explicit:
        return explicit
    if default_backend() == "file":
        return ""
    return "sqlite:///" + str(default_db_path()).lstrip("/")


def run_id_for(slug: str, schema: str, config_pack: str = "", stamp: str = "") -> str:
    """Run id mirroring the old filename, so migrated and new runs read alike."""
    parts = [stamp] if stamp else []
    parts.append(config_pack) if config_pack else None
    parts.append(schema)
    return "_".join([p for p in parts if p]) if stamp else "_".join(
        [p for p in (slug, config_pack, schema) if p]
    )


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------
class StateStore(Protocol):
    def load(self, handle: StateHandle) -> tuple[dict[str, Any], Token]: ...
    def save(self, handle: StateHandle, doc: dict[str, Any], token: Token) -> Token: ...
    def create(self, handle: StateHandle, doc: dict[str, Any]) -> Token: ...
    def exists(self, handle: StateHandle) -> bool: ...
    def list_runs(self, handle: StateHandle) -> list[dict[str, Any]]: ...


def open_store(handle: str | os.PathLike[str] | StateHandle) -> tuple[StateStore, StateHandle]:
    """Resolve a handle and return the store that serves it."""
    h = parse_handle(handle)
    if h.scheme == "file":
        return FileStore(), h
    if h.scheme == "sqlite":
        return SqliteStore(), h
    if h.scheme == "postgresql":
        return PostgresStore(), h
    raise ValueError(f"no store for scheme {h.scheme!r}")


# ---------------------------------------------------------------------------
# File backend — today's behaviour, unchanged
# ---------------------------------------------------------------------------
class FileStore:
    """One YAML document per run. The token is the pre-write bytes.

    Behaviour is deliberately byte-identical to `parser.safe_write_yaml`:
    write, re-parse, restore the previous bytes if the re-parse fails. The only
    addition is that a changed file is now detected (StateConflictError) rather
    than silently overwritten.
    """

    def load(self, handle: StateHandle) -> tuple[dict[str, Any], Token]:
        path = Path(handle.location)
        if not path.is_file():
            raise StateNotFoundError(f"state.yaml not found: {path}")
        pre = path.read_bytes()
        doc = yaml.safe_load(pre.decode("utf-8")) or {}
        if not isinstance(doc, dict):
            raise ValueError(f"state.yaml is not a YAML mapping: {path}")
        return doc, pre

    def save(self, handle: StateHandle, doc: dict[str, Any], token: Token) -> Token:
        path = Path(handle.location)
        if token is not None and path.is_file():
            current = path.read_bytes()
            if current != token:
                raise StateConflictError(
                    f"{path} changed since it was read — reload and retry"
                )
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False,
                           allow_unicode=True)
        try:
            with open(path, encoding="utf-8") as f:
                yaml.safe_load(f)
        except yaml.YAMLError:
            if token is not None:
                path.write_bytes(token)
            raise
        return path.read_bytes()

    def create(self, handle: StateHandle, doc: dict[str, Any]) -> Token:
        path = Path(handle.location)
        path.parent.mkdir(parents=True, exist_ok=True)
        return self.save(handle, doc, None)

    def exists(self, handle: StateHandle) -> bool:
        return Path(handle.location).is_file()

    def list_runs(self, handle: StateHandle) -> list[dict[str, Any]]:
        base = Path(handle.location)
        root = base if base.is_dir() else base.parent
        out = []
        for p in sorted(root.rglob("*_state.yaml")):
            try:
                doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            out.append(_summary(str(p), doc))
        return out


# ---------------------------------------------------------------------------
# SQL backends
# ---------------------------------------------------------------------------
_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    slug          TEXT,
    change_id     TEXT,
    ticket_id     TEXT,
    schema_name   TEXT,
    config_pack   TEXT,
    status        TEXT,
    repo_root     TEXT,
    worktree_path TEXT,
    branch        TEXT,
    doc           TEXT NOT NULL,
    version       INTEGER NOT NULL,
    created_at    TEXT,
    updated_at    TEXT,
    tenant_id     TEXT NOT NULL DEFAULT 'default'
)
"""
# Derived index for cross-run reporting. Rebuilt on every save; never the
# authority — `runs.doc` is.
_DDL_HISTORY = """
CREATE TABLE IF NOT EXISTS step_history (
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    step_id     TEXT,
    phase       TEXT,
    status      TEXT,
    agent       TEXT,
    attempt     INTEGER,
    started_at  TEXT,
    ended_at    TEXT,
    model       TEXT,
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    cache_read_input_tokens     INTEGER,
    cache_creation_input_tokens INTEGER,
    cost_usd    REAL,
    duration_ms INTEGER,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY (run_id, seq)
)
"""
# Learn proposes scenario rows; a human (or the pack's persist-learnings step)
# marks them accepted; `orchestrator pack publish-scenarios` exports the
# accepted ones into the step's scenarios/train.jsonl. Plan phase 3.3.
_DDL_LEARN_RESULTS = """
CREATE TABLE IF NOT EXISTS learn_results (
    run_id       TEXT,
    step_id      TEXT,
    proposed_row TEXT,
    accepted     INTEGER,
    created_at   TEXT
)
"""
_DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_runs_slug ON runs(slug)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_hist_step ON step_history(step_id)",
    "CREATE INDEX IF NOT EXISTS idx_learn_step ON learn_results(step_id)",
)

ENV_TENANT = "ORCHESTRATOR_TENANT"
DEFAULT_TENANT = "default"

#: Columns added after the first release — `_ensure_schema` back-fills them on
#: every connect so an existing db upgrades in place without a migrate step.
_ADDED_COLUMNS = (
    ("runs", "tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ("step_history", "tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
)


def current_tenant() -> str:
    """Tenant new runs are written under (ORCHESTRATOR_TENANT, else 'default')."""
    return (os.environ.get(ENV_TENANT) or "").strip() or DEFAULT_TENANT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary(run_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "slug": doc.get("slug") or "",
        "change_id": doc.get("change_id") or "",
        "ticket_id": doc.get("ticket_id") or "",
        "schema": doc.get("schema") or "",
        "status": doc.get("status") or "",
        "steps": len(doc.get("step_history") or []),
    }


def _history_rows(doc: dict[str, Any]) -> list[tuple]:
    rows = []
    for seq, entry in enumerate(doc.get("step_history") or []):
        if not isinstance(entry, dict):
            continue
        u = entry.get("usage") or {}
        rows.append((
            seq,
            entry.get("step_id"), entry.get("phase"), entry.get("status"),
            entry.get("agent"), entry.get("attempt"),
            entry.get("started_at"), entry.get("ended_at"),
            u.get("model"),
            u.get("input_tokens") or 0, u.get("output_tokens") or 0,
            u.get("cache_read_input_tokens") or 0,
            u.get("cache_creation_input_tokens") or 0,
            u.get("cost_usd"), u.get("duration_ms"),
        ))
    return rows


class _SqlStoreBase:
    """Shared logic; subclasses supply a DB-API connection and a placeholder."""

    ph = "?"

    def _connect(self, handle: StateHandle):  # pragma: no cover - overridden
        raise NotImplementedError

    def _ensure_schema(self, conn) -> None:
        cur = conn.cursor()
        cur.execute(_DDL_RUNS)
        cur.execute(_DDL_HISTORY)
        cur.execute(_DDL_LEARN_RESULTS)
        self._add_missing_columns(conn)
        for ddl in _DDL_INDEXES:
            cur.execute(ddl)

    def _existing_columns(self, conn, table: str) -> set[str]:  # pragma: no cover
        raise NotImplementedError

    def _add_missing_columns(self, conn) -> None:
        """In-place upgrade for dbs created before a column existed.

        `_ensure_schema` runs on every connect, so this has to be idempotent:
        it asks the db which columns are there and only ALTERs the gaps.
        """
        cur = conn.cursor()
        for table, column, decl in _ADDED_COLUMNS:
            if column in self._existing_columns(conn, table):
                continue
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _q(self, sql: str) -> str:
        return sql if self.ph == "?" else sql.replace("?", "%s")

    def _require_run_id(self, handle: StateHandle) -> str:
        if not handle.run_id:
            raise ValueError(
                f"{handle.raw!r} names a store but no run — append '#<run-id>' "
                f"(e.g. sqlite:///path/state.db#orc-123)"
            )
        return handle.run_id

    def load(self, handle: StateHandle) -> tuple[dict[str, Any], Token]:
        run_id = self._require_run_id(handle)
        with self._connect(handle) as conn:
            self._ensure_schema(conn)
            cur = conn.cursor()
            cur.execute(self._q("SELECT doc, version FROM runs WHERE run_id = ?"), (run_id,))
            row = cur.fetchone()
        if row is None:
            raise StateNotFoundError(f"no run {run_id!r} in {handle.location}")
        return json.loads(row[0]), int(row[1])

    def create(self, handle: StateHandle, doc: dict[str, Any]) -> Token:
        run_id = self._require_run_id(handle)
        now = _now()
        tenant = current_tenant()
        with self._connect(handle) as conn:
            self._ensure_schema(conn)
            cur = conn.cursor()
            cur.execute(
                self._q("INSERT INTO runs (run_id, slug, change_id, ticket_id, "
                        "schema_name, config_pack, status, repo_root, worktree_path, "
                        "branch, doc, version, created_at, updated_at, tenant_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
                (run_id, doc.get("slug"), doc.get("change_id"), doc.get("ticket_id"),
                 doc.get("schema"), doc.get("config_pack"), doc.get("status"),
                 doc.get("repo_root"), doc.get("worktree_path"), doc.get("branch"),
                 json.dumps(doc), 1, now, now, tenant),
            )
            self._write_history(conn, run_id, doc, tenant)
        return 1

    def save(self, handle: StateHandle, doc: dict[str, Any], token: Token) -> Token:
        run_id = self._require_run_id(handle)
        new_version = (int(token) if token is not None else 0) + 1
        with self._connect(handle) as conn:
            self._ensure_schema(conn)
            cur = conn.cursor()
            if token is None:
                cur.execute(self._q("SELECT version FROM runs WHERE run_id = ?"), (run_id,))
                if cur.fetchone() is None:
                    return self.create(handle, doc)
            cur.execute(
                self._q("UPDATE runs SET slug=?, change_id=?, ticket_id=?, schema_name=?, "
                        "config_pack=?, status=?, repo_root=?, worktree_path=?, branch=?, "
                        "doc=?, version=?, updated_at=? "
                        "WHERE run_id=? AND version=?"),
                (doc.get("slug"), doc.get("change_id"), doc.get("ticket_id"),
                 doc.get("schema"), doc.get("config_pack"), doc.get("status"),
                 doc.get("repo_root"), doc.get("worktree_path"), doc.get("branch"),
                 json.dumps(doc), new_version, _now(), run_id, int(token)),
            )
            if cur.rowcount != 1:
                raise StateConflictError(
                    f"run {run_id!r} changed since it was read "
                    f"(expected version {token}) — reload and retry"
                )
            cur.execute(self._q("SELECT tenant_id FROM runs WHERE run_id = ?"), (run_id,))
            row = cur.fetchone()
            self._write_history(conn, run_id, doc, (row and row[0]) or DEFAULT_TENANT)
        return new_version

    def _write_history(self, conn, run_id: str, doc: dict[str, Any],
                       tenant: str = DEFAULT_TENANT) -> None:
        cur = conn.cursor()
        cur.execute(self._q("DELETE FROM step_history WHERE run_id = ?"), (run_id,))
        rows = _history_rows(doc)
        if rows:
            cur.executemany(
                self._q(
                    "INSERT INTO step_history "
                    "(run_id, seq, step_id, phase, status, agent, attempt, started_at, "
                    "ended_at, model, input_tokens, output_tokens, "
                    "cache_read_input_tokens, cache_creation_input_tokens, cost_usd, "
                    "duration_ms, tenant_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                ),
                [(run_id, *r, tenant) for r in rows],
            )

    # -- learn results (plan phase 3.3) -----------------------------------
    def add_learn_row(self, handle: StateHandle, run_id: str, step_id: str,
                      proposed_row: dict[str, Any], accepted: bool | None) -> None:
        """Record one scenario row proposed by a learn step."""
        with self._connect(handle) as conn:
            self._ensure_schema(conn)
            conn.cursor().execute(
                self._q("INSERT INTO learn_results "
                        "(run_id, step_id, proposed_row, accepted, created_at) "
                        "VALUES (?,?,?,?,?)"),
                (run_id, step_id, json.dumps(proposed_row, sort_keys=True),
                 None if accepted is None else int(bool(accepted)), _now()),
            )

    def list_learn_rows(self, handle: StateHandle,
                        accepted: bool | None = None) -> list[dict[str, Any]]:
        """Proposed rows, newest last. `accepted=True` filters to the exported set."""
        sql = ("SELECT run_id, step_id, proposed_row, accepted, created_at "
               "FROM learn_results")
        params: tuple = ()
        if accepted is not None:
            sql += " WHERE accepted = ?"
            params = (int(bool(accepted)),)
        sql += " ORDER BY created_at"
        with self._connect(handle) as conn:
            self._ensure_schema(conn)
            cur = conn.cursor()
            cur.execute(self._q(sql), params)
            rows = cur.fetchall()
        return [
            {
                "run_id": r[0],
                "step_id": r[1],
                "proposed_row": json.loads(r[2]) if r[2] else {},
                "accepted": None if r[3] is None else bool(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]

    def exists(self, handle: StateHandle) -> bool:
        try:
            self.load(handle)
            return True
        except (StateNotFoundError, ValueError):
            return False

    def list_runs(self, handle: StateHandle) -> list[dict[str, Any]]:
        with self._connect(handle) as conn:
            self._ensure_schema(conn)
            cur = conn.cursor()
            cur.execute("SELECT run_id, doc FROM runs ORDER BY created_at")
            rows = cur.fetchall()
        return [_summary(r[0], json.loads(r[1])) for r in rows]


class SqliteStore(_SqlStoreBase):
    """Embedded, no daemon, stdlib only. WAL so readers never block the writer."""

    ph = "?"

    def _connect(self, handle: StateHandle):
        path = Path(handle.location)
        path.parent.mkdir(parents=True, exist_ok=True)
        # timeout: a concurrent writer waits rather than failing instantly. The
        # version check still catches a genuine lost update; this only smooths
        # over lock contention.
        conn = sqlite3.connect(str(path), timeout=30.0, isolation_level="DEFERRED")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _existing_columns(self, conn, table: str) -> set[str]:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}


class PostgresStore(_SqlStoreBase):
    """Same schema and same optimistic-concurrency contract, over psycopg.

    Untested in this repo — there is no Postgres in CI yet. The SQL is
    placeholder-translated from the SQLite statements and the concurrency
    contract is identical (`UPDATE ... WHERE version = ?`), but treat this as
    unproven until a live integration test exists.
    """

    ph = "%s"

    def _connect(self, handle: StateHandle):
        try:
            import psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "postgresql:// state URLs need psycopg — "
                "install it with `uv add psycopg[binary]`"
            ) from exc
        return psycopg.connect(handle.location)

    def _existing_columns(self, conn, table: str) -> set[str]:
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Convenience API used by the engine
# ---------------------------------------------------------------------------
def load_doc(handle: str | StateHandle) -> tuple[dict[str, Any], Token, StateHandle]:
    store, h = open_store(handle)
    doc, token = store.load(h)
    return doc, token, h


def save_doc(handle: str | StateHandle, doc: dict[str, Any], token: Token) -> Token:
    store, h = open_store(handle)
    return store.save(h, doc, token)


def add_learn_row(handle: str | StateHandle, run_id: str, step_id: str,
                  proposed_row: dict[str, Any], accepted: bool | None = None) -> None:
    """Record a learn-proposed scenario row. Callers rarely hold a store."""
    store, h = open_store(handle)
    store.add_learn_row(h, run_id, step_id, proposed_row, accepted)


def list_learn_rows(handle: str | StateHandle,
                    accepted: bool | None = None) -> list[dict[str, Any]]:
    """Learn-proposed rows; `accepted=True` is what `pack publish-scenarios` exports."""
    store, h = open_store(handle)
    return store.list_learn_rows(h, accepted)


def project_yaml(handle: str | StateHandle, dest: str | os.PathLike[str]) -> Path:
    """Materialize a run as a read-only `state.yaml` at `dest`.

    Eight pack scripts read the state file directly by path
    (create-worktree, remove-worktree, merge-to-main, ticket-done,
    load-ticket-context, check-rerun, mark-change-completed, workflow-report),
    and the `learn` prompt tells the agent to read it too. Under a SQL store
    there is no such file, so the engine writes this projection before a script
    step runs and keeps `STATE_YAML_PATH` pointing at it.

    Writes go the other way, through the `state_patch` channel the scripts
    already use — so the projection can stay strictly read-only and no script
    needs to learn about the store. The two scripts that currently write the
    file directly (`mark_change_completed.py`, `check_rerun.py`) are the
    exception and must be converted to emit `state_patch` instead.
    """
    doc, _token, _h = load_doc(handle)
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False,
                       allow_unicode=True)
    return out


def import_yaml(src: str | os.PathLike[str], dest_url: str, run_id: str = "") -> str:
    """Copy a state.yaml into a store. Returns the resulting handle string."""
    src_path = Path(src)
    doc = yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{src_path} is not a YAML mapping")
    rid = run_id or src_path.stem.replace("_state", "") or src_path.stem
    target = f"{dest_url}#{rid}"
    store, h = open_store(target)
    if store.exists(h):
        raise FileExistsError(f"run {rid!r} already exists in {h.location}")
    store.create(h, doc)
    return target
