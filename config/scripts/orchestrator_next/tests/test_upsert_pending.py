"""
Tests for upsert_pending_step_event — FR-1, AC-1, NFR-3, NFR-4.

Scenarios:
  (a) Single-row insert with NULL cost/usage: confirms status='in_progress',
      all nullable columns are NULL, started_at is set, ended_at is NULL,
      tool_calls_json is NULL, no tool_calls fan-out rows.
  (b) Idempotent re-insert: two calls with the same PK leave exactly one row.
  (c) Slug-guard rejection: invalid change_id raises ValueError.
"""
from __future__ import annotations

import pytest
import duckdb

from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event


@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


class TestUpsertPendingStepEvent:

    def test_single_row_insert_with_null_cost_usage(self, in_memory_db):
        """(a) Insert a fresh pending row; all cost/usage columns must be NULL."""
        db = in_memory_db
        upsert_pending_step_event(
            db,
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="T-1",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )

        row = db.execute(
            """
            SELECT
                status, started_at, ended_at,
                agent_id, duration_ms,
                model, input_tokens, output_tokens,
                cache_read_input_tokens, cache_creation_input_tokens,
                cost_usd, turns,
                tool_calls_json, artifacts_json, escalation_json
            FROM step_events
            WHERE repo_root = ? AND change_id = ? AND phase = ?
              AND step_id = ? AND attempt = ?
            """,
            ["/repo/root", "my-feature", "implement", "T-1", 1],
        ).fetchone()

        assert row is not None, "Expected one row in step_events"
        (
            status, started_at, ended_at,
            agent_id, duration_ms,
            model, input_tokens, output_tokens,
            cache_read_input_tokens, cache_creation_input_tokens,
            cost_usd, turns,
            tool_calls_json, artifacts_json, escalation_json,
        ) = row

        assert status == "in_progress"
        assert started_at is not None
        assert ended_at is None
        assert agent_id is None
        assert duration_ms is None
        assert model is None
        assert input_tokens is None
        assert output_tokens is None
        assert cache_read_input_tokens is None
        assert cache_creation_input_tokens is None
        assert cost_usd is None
        assert turns is None
        assert tool_calls_json is None
        assert artifacts_json is None
        assert escalation_json is None

        # Confirm no tool_calls fan-out rows were created.
        tc_count = db.execute(
            """
            SELECT COUNT(*) FROM tool_calls
            WHERE repo_root = ? AND change_id = ? AND step_id = ? AND attempt = ?
            """,
            ["/repo/root", "my-feature", "T-1", 1],
        ).fetchone()[0]
        assert tc_count == 0, "upsert_pending_step_event must not fan-out to tool_calls table"

    def test_idempotent_reinsertion_leaves_one_row(self, in_memory_db):
        """(b) Two calls with the same PK must leave exactly one row."""
        db = in_memory_db
        kwargs = dict(
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="T-1",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        upsert_pending_step_event(db, **kwargs)
        upsert_pending_step_event(db, **kwargs)

        count = db.execute(
            """
            SELECT COUNT(*) FROM step_events
            WHERE repo_root = ? AND change_id = ? AND phase = ?
              AND step_id = ? AND attempt = ? AND status = 'in_progress'
            """,
            ["/repo/root", "my-feature", "implement", "T-1", 1],
        ).fetchone()[0]
        assert count == 1, "Idempotent re-insert must not duplicate rows"

    def test_slug_guard_rejects_invalid_change_id(self, in_memory_db):
        """(c) change_id with uppercase/slash raises ValueError matching slug guard."""
        db = in_memory_db
        with pytest.raises(ValueError, match="slug guard"):
            upsert_pending_step_event(
                db,
                repo_root="/repo/root",
                change_id="Invalid-Upper",
                phase="implement",
                step_id="T-1",
                attempt=1,
                agent_name="developer",
                started_at="2024-01-01T00:00:00Z",
            )
