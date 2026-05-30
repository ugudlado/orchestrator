"""
DuckDB step_events DDL + upsert logic.

Public API:
  ensure_schema(db)                        — CREATE TABLE IF NOT EXISTS + index
  upsert_step_event(db, entry, context)   — INSERT OR REPLACE for one entry

Design constraints:
  - All SQL uses parameterised duckdb.execute(sql, params) — no string interpolation.
  - change_id is validated against ^[a-z0-9][a-z0-9-]*$ before any INSERT.
  - Primary key: (repo_root, change_id, phase, step_id, attempt, status) — 6 columns.
  - status is in the PK to preserve escalation audit trail (two rows at same
    attempt: one escalate_to_architect, one completed).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from orchestrator_next.parser import StepHistoryEntry

# Slug guard: change_id must match ^[a-z0-9][a-z0-9-]*$
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SLUG_GUARD_MSG = (
    "Must match ^[a-z0-9][a-z0-9-]*$ (lowercase alphanumeric and hyphens only, "
    "no leading hyphen)."
)


def _validate_change_id(change_id: str) -> None:
    if not _SLUG_RE.match(change_id):
        raise ValueError(
            f"change_id '{change_id}' violates slug guard. {_SLUG_GUARD_MSG}"
        )

_DDL_STEP_EVENTS = """
CREATE TABLE IF NOT EXISTS step_events (
  repo_root   VARCHAR NOT NULL,
  change_id   VARCHAR NOT NULL,
  phase       VARCHAR NOT NULL,
  step_id     VARCHAR NOT NULL,
  attempt     INTEGER NOT NULL,
  agent_name  VARCHAR NOT NULL,
  agent_id    VARCHAR,
  status      VARCHAR NOT NULL,
  schema_name VARCHAR,
  started_at  TIMESTAMP,
  ended_at    TIMESTAMP,
  duration_ms BIGINT,
  model                        VARCHAR,
  input_tokens                 BIGINT,
  output_tokens                BIGINT,
  cache_read_input_tokens      BIGINT,
  cache_creation_input_tokens  BIGINT,
  cost_usd                     DOUBLE,
  turns                        BIGINT,
  tool_calls_json  VARCHAR,
  artifacts_json   VARCHAR,
  escalation_json  VARCHAR,
  upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
)
"""

_DDL_TOOL_CALLS = """
CREATE TABLE IF NOT EXISTS tool_calls (
  repo_root     TEXT    NOT NULL,
  change_id     TEXT    NOT NULL,
  phase         TEXT    NOT NULL,
  step_id       TEXT    NOT NULL,
  attempt       INTEGER NOT NULL,
  agent_name    TEXT    NOT NULL,
  tool_name     TEXT    NOT NULL,
  is_mcp        BOOLEAN NOT NULL,
  call_seq      INTEGER NOT NULL,
  called_at     TEXT,
  duration_ms   BIGINT,
  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, call_seq)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_step_events_change
  ON step_events(repo_root, change_id)
"""

_CREATE_TOOL_CALLS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tool_calls_change
  ON tool_calls(repo_root, change_id)
"""

_DDL_FEATURE_COMPLEXITY = """
CREATE TABLE IF NOT EXISTS feature_complexity (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  complexity   VARCHAR,
  schema_name  VARCHAR,
  started_at   TIMESTAMP,
  completed_at TIMESTAMP,
  upserted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id)
)
"""

_INSERT_FEATURE_COMPLEXITY = """
INSERT OR REPLACE INTO feature_complexity
  (repo_root, change_id, complexity, schema_name, started_at, completed_at)
VALUES (?, ?, ?, ?, ?, ?)
"""

_DDL_FEATURE_METRICS = """
CREATE TABLE IF NOT EXISTS feature_metrics (
  repo_root          VARCHAR NOT NULL,
  change_id          VARCHAR NOT NULL,
  schema_name        VARCHAR,
  -- Resolution
  tasks_total        INTEGER,
  tasks_planned      INTEGER,
  tasks_added        INTEGER,
  tasks_completed    INTEGER,
  tasks_failed       INTEGER,
  resolve_rate       DOUBLE,
  pass_at_1          DOUBLE,
  pass_at_2          DOUBLE,
  regressions        INTEGER,
  regression_rate    DOUBLE,
  -- Retries / interventions
  retries_total      INTEGER,
  human_interventions INTEGER,
  -- Churn
  files_changed      INTEGER,
  insertions         INTEGER,
  deletions          INTEGER,
  total_commits      INTEGER,
  rework_commits     INTEGER,
  rework_rate        DOUBLE,
  -- Reviews
  review_scores_json VARCHAR,
  review_score_avg   DOUBLE,
  -- Timing
  wall_clock_minutes DOUBLE,
  -- Audit
  source             VARCHAR,
  computed_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id)
)
"""

_INSERT_FEATURE_METRICS = """
INSERT OR REPLACE INTO feature_metrics (
  repo_root, change_id, schema_name,
  tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
  resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
  retries_total, human_interventions,
  files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
  review_scores_json, review_score_avg,
  wall_clock_minutes,
  source
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_DELETE_TOOL_CALLS = """
DELETE FROM tool_calls
WHERE repo_root = ? AND change_id = ? AND phase = ? AND step_id = ? AND attempt = ?
"""

_INSERT_TOOL_CALL = """
INSERT OR REPLACE INTO tool_calls (
  repo_root, change_id, phase, step_id, attempt,
  agent_name, tool_name, is_mcp, call_seq, called_at, duration_ms
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_OR_REPLACE = """
INSERT OR REPLACE INTO step_events (
  repo_root,
  change_id,
  phase,
  step_id,
  attempt,
  agent_name,
  agent_id,
  status,
  started_at,
  ended_at,
  duration_ms,
  model,
  input_tokens,
  output_tokens,
  cache_read_input_tokens,
  cache_creation_input_tokens,
  cost_usd,
  turns,
  tool_calls_json,
  artifacts_json,
  escalation_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


_SUM_COST_SQL = """
SELECT COALESCE(SUM(cost_usd), 0.0)
FROM step_events
WHERE repo_root = ? AND change_id = ?
"""


def sum_cost_usd(db, context: dict) -> float:
    """
    Return the sum of gen_ai_usage_cost_usd for (repo_root, change_id).

    Args:
        db:      open duckdb.DuckDBPyConnection (schema already ensured)
        context: dict with keys 'repo_root' and 'change_id'

    Returns:
        float sum of gen_ai_usage_cost_usd, or 0.0 if no rows / all NULL.

    Raises:
        ValueError: if change_id violates the slug guard.
    """
    change_id: str = context["change_id"]
    repo_root: str = context.get("repo_root", "")

    _validate_change_id(change_id)

    row = db.execute(_SUM_COST_SQL, [repo_root, change_id]).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


_STEP_EVENTS_RENAMES = [
    ("gen_ai_request_model",                 "model"),
    ("gen_ai_usage_input_tokens",            "input_tokens"),
    ("gen_ai_usage_output_tokens",           "output_tokens"),
    ("gen_ai_usage_cache_read_input_tokens", "cache_read_input_tokens"),
    ("gen_ai_usage_cost_usd",                "cost_usd"),
]

_INDEX_NAME = "idx_step_events_change"


def _migrate_step_events(db) -> None:
    """Migrate existing step_events tables.

    Two migrations handled idempotently:
      1. Rename otel-prefixed columns to plain names (legacy, pre-HL-287).
         Drops idx_step_events_change before renaming because DuckDB refuses
         ALTER TABLE ... RENAME COLUMN while an index depends on the table.
      2. Add cache_creation_input_tokens column if missing (ADD COLUMN is
         safe without dropping the index).

    The caller's ensure_schema() recreates the index via _CREATE_INDEX
    (CREATE INDEX IF NOT EXISTS) after this function returns.
    """
    try:
        existing = {row[0] for row in db.execute("DESCRIBE step_events").fetchall()}
    except Exception:
        return
    needs_rename = any(
        old in existing and new not in existing
        for old, new in _STEP_EVENTS_RENAMES
    )
    if needs_rename:
        db.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
        for old, new in _STEP_EVENTS_RENAMES:
            if old in existing and new not in existing:
                db.execute(f"ALTER TABLE step_events RENAME COLUMN {old} TO {new}")
        existing = {row[0] for row in db.execute("DESCRIBE step_events").fetchall()}
    if "cache_creation_input_tokens" not in existing:
        db.execute("ALTER TABLE step_events ADD COLUMN cache_creation_input_tokens BIGINT")
        existing.add("cache_creation_input_tokens")
    if "agent_id" not in existing:
        db.execute("ALTER TABLE step_events ADD COLUMN agent_id VARCHAR")
    if "turns" not in existing:
        db.execute("ALTER TABLE step_events ADD COLUMN turns BIGINT")


def _migrate_tool_calls(db) -> None:
    """Add duration_ms to existing tool_calls tables idempotently."""
    try:
        existing = {row[0] for row in db.execute("DESCRIBE tool_calls").fetchall()}
    except Exception:
        return
    if "duration_ms" not in existing:
        db.execute("ALTER TABLE tool_calls ADD COLUMN duration_ms BIGINT")


# ---------------------------------------------------------------------------
# SQL migration runner (FR-1, NFR-2)
# ---------------------------------------------------------------------------

_DDL_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  name       VARCHAR PRIMARY KEY,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _migrations_dir() -> Path:
    """Return the path to the migrations directory co-located with upsert.py."""
    return Path(__file__).parent / "migrations"


def _run_migrations(db) -> list[str]:
    """Idempotent migration runner.

    Creates the schema_migrations tracking table if absent, discovers every
    *.sql file under _migrations_dir() in lexical order, skips files whose
    basename is already recorded, and executes each remaining file in a
    per-file BEGIN/COMMIT transaction. On exception the transaction is rolled
    back and the exception re-raised — the migration name is never recorded.

    Args:
        db: open DuckDB RW connection (schema_migrations DDL is written here).

    Returns:
        List of migration names applied during this call (possibly empty).

    Raises:
        Exception: re-raises any error from executing a migration SQL file.
    """
    db.execute(_DDL_SCHEMA_MIGRATIONS)
    applied = {
        row[0]
        for row in db.execute("SELECT name FROM schema_migrations").fetchall()
    }
    applied_now: list[str] = []
    for path in sorted(_migrations_dir().glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text()
        db.execute("BEGIN")
        try:
            db.execute(sql)
            db.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)", [path.name]
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        applied_now.append(path.name)
    return applied_now


def ensure_schema(db) -> None:
    """
    Create the step_events, tool_calls, and feature_complexity tables and indexes
    if they do not exist. Migrates otel column names to plain names on existing tables.

    Safe to call multiple times (idempotent via IF NOT EXISTS).
    """
    db.execute(_DDL_STEP_EVENTS)
    _migrate_step_events(db)
    db.execute(_CREATE_INDEX)
    db.execute(_DDL_TOOL_CALLS)
    _migrate_tool_calls(db)
    db.execute(_CREATE_TOOL_CALLS_INDEX)
    db.execute(_DDL_FEATURE_COMPLEXITY)  # HL-291
    db.execute(_DDL_FEATURE_METRICS)
    # New — runs after legacy ALTERs so order is deterministic.
    _run_migrations(db)


def upsert_feature_complexity(
    db,
    repo_root: str,
    change_id: str,
    complexity: str | None,
    schema_name: str | None,
    started_at,
    completed_at,
) -> None:
    """
    Upsert one row into feature_complexity keyed on (repo_root, change_id).

    A None complexity still writes the row — the row records the feature's
    existence; complexity is NULL-able (FR-4).

    Args:
        db:           open duckdb.DuckDBPyConnection (schema already ensured)
        repo_root:    absolute path to the repo root
        change_id:    feature change identifier
        complexity:   one of {XS,S,M,L,XL} or None
        schema_name:  state.yaml `schema` value (e.g. "feature")
        started_at:   ISO timestamp or None
        completed_at: ISO timestamp or None
    """
    db.execute(_INSERT_FEATURE_COMPLEXITY, [
        repo_root, change_id, complexity, schema_name, started_at, completed_at,
    ])


def upsert_feature_metrics(
    db,
    repo_root: str,
    change_id: str,
    **fields,
) -> None:
    """
    Upsert one row into feature_metrics keyed on (repo_root, change_id).

    Uses INSERT OR REPLACE so calling with the same (repo_root, change_id)
    replaces the existing row.

    Args:
        db:        open duckdb.DuckDBPyConnection (schema already ensured)
        repo_root: absolute path to the repo root
        change_id: feature change identifier (validated against slug guard)
        **fields:  any subset of feature_metrics columns (unmapped fields silently
                   become None so callers don't need to supply every column)

    Raises:
        ValueError: if change_id violates the slug guard
    """
    _validate_change_id(change_id)

    db.execute(_INSERT_FEATURE_METRICS, [
        repo_root,
        change_id,
        fields.get("schema_name"),
        fields.get("tasks_total"),
        fields.get("tasks_planned"),
        fields.get("tasks_added"),
        fields.get("tasks_completed"),
        fields.get("tasks_failed"),
        fields.get("resolve_rate"),
        fields.get("pass_at_1"),
        fields.get("pass_at_2"),
        fields.get("regressions"),
        fields.get("regression_rate"),
        fields.get("retries_total"),
        fields.get("human_interventions"),
        fields.get("files_changed"),
        fields.get("insertions"),
        fields.get("deletions"),
        fields.get("total_commits"),
        fields.get("rework_commits"),
        fields.get("rework_rate"),
        fields.get("review_scores_json"),
        fields.get("review_score_avg"),
        fields.get("wall_clock_minutes"),
        fields.get("source"),
    ])


def _fan_out_tool_calls(
    db,
    *,
    repo_root: str,
    change_id: str,
    phase: str,
    step_id: str,
    attempt: int,
    agent_name: str,
    usage: dict[str, Any] | None,
) -> None:
    """Write per-call tool_calls rows; DELETE first so retries drop orphans."""
    db.execute(_DELETE_TOOL_CALLS, [repo_root, change_id, phase, step_id, attempt])
    usage = usage or {}
    tool_calls_detail = usage.get("tool_calls_detail")
    if isinstance(tool_calls_detail, list) and tool_calls_detail:
        call_seq = 1
        for call in tool_calls_detail:
            if not isinstance(call, dict):
                continue
            tn = call.get("tool_name")
            if not tn:
                continue
            db.execute(_INSERT_TOOL_CALL, [
                repo_root, change_id, phase, step_id, attempt,
                agent_name, tn, bool(call.get("is_mcp") or tn.startswith("mcp__")),
                call_seq, call.get("started_at"), call.get("duration_ms"),
            ])
            call_seq += 1
        return

    usage_tools = usage.get("tool_calls")
    if not isinstance(usage_tools, dict):
        return
    call_seq = 1
    for tool_name in sorted(usage_tools.keys()):
        count = usage_tools[tool_name]
        if not isinstance(count, int) or count < 1:
            continue
        is_mcp = tool_name.startswith("mcp__")
        for _ in range(count):
            db.execute(_INSERT_TOOL_CALL, [
                repo_root, change_id, phase, step_id, attempt,
                agent_name, tool_name, is_mcp, call_seq, None, None,
            ])
            call_seq += 1


def upsert_step_event(
    db,
    entry: StepHistoryEntry,
    context: dict[str, Any],
) -> None:
    """
    Upsert one step_history entry into step_events.

    Args:
        db:      open duckdb.DuckDBPyConnection
        entry:   parsed StepHistoryEntry
        context: dict with keys 'repo_root' and 'change_id'

    Raises:
        ValueError: if change_id violates the slug guard
    """
    change_id: str = context["change_id"]
    repo_root: str = context.get("repo_root", "")

    _validate_change_id(change_id)

    # Attempt defaults to 1 if not set
    attempt: int = entry.attempt if entry.attempt is not None else 1

    usage = entry.usage or {}

    # Serialise tool_calls dict to JSON string if present
    tool_calls_raw = usage.get("tool_calls")
    tool_calls_json = json.dumps(tool_calls_raw, sort_keys=True) if tool_calls_raw else None

    artifacts = entry.raw.get("artifacts")
    artifacts_json = json.dumps(artifacts) if artifacts else None

    escalation_json = json.dumps(entry.escalation, sort_keys=True) if entry.escalation else None

    # agent_id lives either in usage (explicit passthrough from caller) or in
    # the step_history entry's raw dict (set by the driver when the Agent tool
    # returns an agentId). Either location is fine — we pull whichever exists.
    agent_id = usage.get("agent_id") or entry.raw.get("agent_id")

    params = [
        repo_root,
        change_id,
        entry.phase,
        entry.step_id,
        attempt,
        entry.agent,
        agent_id,
        entry.status,
        entry.started_at,
        entry.ended_at,
        usage.get("duration_ms"),
        usage.get("model"),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cache_creation_input_tokens"),
        usage.get("cost_usd"),
        usage.get("turns"),
        tool_calls_json,
        artifacts_json,
        escalation_json,
    ]

    db.execute(_INSERT_OR_REPLACE, params)

    _fan_out_tool_calls(
        db,
        repo_root=repo_root,
        change_id=change_id,
        phase=entry.phase,
        step_id=entry.step_id,
        attempt=attempt,
        agent_name=entry.agent,
        usage=entry.usage,
    )


def upsert_pending_step_event(
    db,
    *,
    repo_root: str,
    change_id: str,
    phase: str,
    step_id: str,
    attempt: int,
    agent_name: str,
    started_at: str,
) -> None:
    """
    Upsert one in_progress row into step_events for a step that is about to be
    dispatched.  All cost, usage, model, and lifecycle-end columns are NULL.
    status is fixed to 'in_progress' — do not accept it as a parameter.

    Reuses _INSERT_OR_REPLACE; no tool_calls fan-out (pending rows never own
    tool_calls entries — those are written only on terminal upsert_step_event
    calls).

    Args:
        db:         open duckdb.DuckDBPyConnection (schema already ensured)
        repo_root:  absolute path to the repo root
        change_id:  feature change identifier (validated against slug guard)
        phase:      workflow phase name (e.g. "implement")
        step_id:    step identifier (e.g. "T-1")
        attempt:    attempt number (1-based)
        agent_name: agent name (e.g. "developer")
        started_at: ISO-8601 timestamp string for when the step was dispatched

    Raises:
        ValueError: if change_id violates the slug guard.
    """
    _validate_change_id(change_id)

    params = [
        repo_root,
        change_id,
        phase,
        step_id,
        attempt,
        agent_name,
        None,           # agent_id
        "in_progress",  # status — fixed, not a parameter
        started_at,     # started_at
        None,           # ended_at
        None,           # duration_ms
        None,           # model
        None,           # input_tokens
        None,           # output_tokens
        None,           # cache_read_input_tokens
        None,           # cache_creation_input_tokens
        None,           # cost_usd
        None,           # turns
        None,           # tool_calls_json — no fan-out for pending rows
        None,           # artifacts_json
        None,           # escalation_json
    ]
    db.execute(_INSERT_OR_REPLACE, params)
    # No tool_calls fan-out; pending rows have no tool_calls to write.


def upsert_synthetic_event(
    db,
    context: dict[str, Any],
    *,
    agent_name: str,
    step_id: str,
    phase: str,
    usage: dict[str, Any],
    status: str = "completed",
    started_at: Any = None,
    ended_at: Any = None,
) -> None:
    """Upsert a synthetic step_events row without a StepHistoryEntry.

    Used for driver-loop ingestion from JSONL totals — there's no real
    step_history entry, but we want the row in step_events so all rollups
    (per-agent, per-phase, per-feature, per-repo) are pure GROUP BY queries.

    The synthetic row gets attempt=1 and empty artifacts/escalation blobs.
    tool_calls_json is populated from usage['tool_calls'] (a dict) and also
    fanned out to the tool_calls table for per-tool rollups.
    """
    change_id: str = context["change_id"]
    repo_root: str = context.get("repo_root", "")
    _validate_change_id(change_id)

    attempt = 1
    tool_calls_raw = usage.get("tool_calls")
    tool_calls_json = json.dumps(tool_calls_raw, sort_keys=True) if tool_calls_raw else None

    params = [
        repo_root,
        change_id,
        phase,
        step_id,
        attempt,
        agent_name,
        usage.get("agent_id"),
        status,
        started_at,
        ended_at,
        usage.get("duration_ms"),
        usage.get("model"),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cache_creation_input_tokens"),
        usage.get("cost_usd"),
        usage.get("turns"),
        tool_calls_json,
        None,  # artifacts_json
        None,  # escalation_json
    ]
    db.execute(_INSERT_OR_REPLACE, params)

    _fan_out_tool_calls(
        db,
        repo_root=repo_root,
        change_id=change_id,
        phase=phase,
        step_id=step_id,
        attempt=attempt,
        agent_name=agent_name,
        usage=usage,
    )
