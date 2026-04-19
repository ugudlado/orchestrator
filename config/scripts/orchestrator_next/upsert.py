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

import re
import json
from typing import Any

import json
from orchestrator_next.parser import StepHistoryEntry

# Slug guard: change_id must match ^[a-z0-9][a-z0-9-]*$
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

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

    # Slug guard — reject invalid change_id before any query
    if not _SLUG_RE.match(change_id):
        raise ValueError(
            f"change_id '{change_id}' violates slug guard. "
            f"Must match ^[a-z0-9][a-z0-9-]*$ (lowercase alphanumeric and hyphens only, "
            f"no leading hyphen)."
        )

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

    # Slug guard — before any DB operation
    if not _SLUG_RE.match(change_id):
        raise ValueError(
            f"change_id '{change_id}' violates slug guard. "
            f"Must match ^[a-z0-9][a-z0-9-]*$ (lowercase alphanumeric and hyphens only, "
            f"no leading hyphen)."
        )

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

    # Fan out usage.tool_calls into per-call tool_calls rows.
    # Always DELETE first so retries with fewer tools don't leave orphan rows.
    db.execute(_DELETE_TOOL_CALLS, [repo_root, change_id, entry.phase, entry.step_id, attempt])

    usage_tools: dict = {}
    if entry.usage and isinstance(entry.usage.get("tool_calls"), dict):
        usage_tools = entry.usage["tool_calls"]

    # Prefer per-call detail (from JSONL) — it carries wall-clock duration.
    tool_calls_detail = (entry.usage or {}).get("tool_calls_detail") if entry.usage else None
    if isinstance(tool_calls_detail, list) and tool_calls_detail:
        call_seq = 1
        for call in tool_calls_detail:
            if not isinstance(call, dict):
                continue
            tn = call.get("tool_name")
            if not tn:
                continue
            db.execute(_INSERT_TOOL_CALL, [
                repo_root, change_id, entry.phase, entry.step_id, attempt,
                entry.agent, tn, bool(call.get("is_mcp") or tn.startswith("mcp__")),
                call_seq, call.get("started_at"), call.get("duration_ms"),
            ])
            call_seq += 1
    else:
        call_seq = 1
        for tool_name in sorted(usage_tools.keys()):
            count = usage_tools[tool_name]
            if not isinstance(count, int) or count < 1:
                continue
            is_mcp = tool_name.startswith("mcp__")
            for _ in range(count):
                db.execute(_INSERT_TOOL_CALL, [
                    repo_root, change_id, entry.phase, entry.step_id, attempt,
                    entry.agent, tool_name, is_mcp, call_seq, None, None,
                ])
                call_seq += 1


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
    if not _SLUG_RE.match(change_id):
        raise ValueError(
            f"change_id '{change_id}' violates slug guard. "
            f"Must match ^[a-z0-9][a-z0-9-]*$ (lowercase alphanumeric and hyphens only, "
            f"no leading hyphen)."
        )

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

    # Fan out tool_calls the same way upsert_step_event does, so per-tool
    # rollups include driver-loop activity.
    db.execute(_DELETE_TOOL_CALLS, [repo_root, change_id, phase, step_id, attempt])
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
    else:
        usage_tools = tool_calls_raw if isinstance(tool_calls_raw, dict) else {}
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
