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

from orchestrator_next.otel_map import usage_to_otel, serialise_artifacts, serialise_escalation
from orchestrator_next.parser import StepHistoryEntry

# Slug guard: change_id must match ^[a-z0-9][a-z0-9-]*$
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_DDL = """
CREATE TABLE IF NOT EXISTS step_events (
  repo_root   VARCHAR NOT NULL,
  change_id   VARCHAR NOT NULL,
  phase       VARCHAR NOT NULL,
  step_id     VARCHAR NOT NULL,
  attempt     INTEGER NOT NULL,
  agent_name  VARCHAR NOT NULL,
  status      VARCHAR NOT NULL,
  schema_name VARCHAR,
  started_at  TIMESTAMP,
  ended_at    TIMESTAMP,
  duration_ms BIGINT,
  gen_ai_request_model                  VARCHAR,
  gen_ai_usage_input_tokens             BIGINT,
  gen_ai_usage_output_tokens            BIGINT,
  gen_ai_usage_cache_read_input_tokens  BIGINT,
  gen_ai_usage_cost_usd                 DOUBLE,
  tool_calls_json  VARCHAR,
  artifacts_json   VARCHAR,
  escalation_json  VARCHAR,
  upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_step_events_change
  ON step_events(repo_root, change_id)
"""

_INSERT_OR_REPLACE = """
INSERT OR REPLACE INTO step_events (
  repo_root,
  change_id,
  phase,
  step_id,
  attempt,
  agent_name,
  status,
  started_at,
  ended_at,
  duration_ms,
  gen_ai_request_model,
  gen_ai_usage_input_tokens,
  gen_ai_usage_output_tokens,
  gen_ai_usage_cache_read_input_tokens,
  gen_ai_usage_cost_usd,
  tool_calls_json,
  artifacts_json,
  escalation_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def ensure_schema(db) -> None:
    """
    Create the step_events table and index if they do not exist.

    Safe to call multiple times (idempotent via IF NOT EXISTS).
    """
    db.execute(_DDL)
    db.execute(_CREATE_INDEX)


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

    # Map usage short names to OTel columns
    otel = usage_to_otel(entry.usage) if entry.usage else {}

    # Parse timestamps — DuckDB accepts ISO 8601 strings directly
    started_at = entry.started_at
    ended_at = entry.ended_at

    params = [
        repo_root,
        change_id,
        entry.phase,
        entry.step_id,
        attempt,
        entry.agent,
        entry.status,
        started_at,
        ended_at,
        otel.get("duration_ms"),
        otel.get("gen_ai_request_model"),
        otel.get("gen_ai_usage_input_tokens"),
        otel.get("gen_ai_usage_output_tokens"),
        otel.get("gen_ai_usage_cache_read_input_tokens"),
        otel.get("gen_ai_usage_cost_usd"),
        otel.get("tool_calls_json"),
        serialise_artifacts(entry.raw.get("artifacts")),
        serialise_escalation(entry.escalation),
    ]

    db.execute(_INSERT_OR_REPLACE, params)
