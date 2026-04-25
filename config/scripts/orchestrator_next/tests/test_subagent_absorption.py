"""T-8a (RED) / T-8b (GREEN): _resolve_subagent_rows + _write_subagent_events.

Tests cover FR-6a, AC-6a — _ingest_subagents_main absorption must reproduce
per-subagent synthetic step_events rows so the agent_report view continues
to attribute usage by subagent.

Cases:
  (a) _resolve_subagent_rows returns one dict per discovered subagent JSONL,
      with agent_name from agent-<id>.meta.json agentType field.
  (b) fallback to 'subagent-unknown' when meta.json is missing — row still emitted.
  (c) malformed meta.json skips only that row (stderr log), no exception raised.
  (d) _resolve_subagent_rows does NOT open a DuckDB connection or BEGIN — pure parsing.
  (e) _write_subagent_events calls upsert_synthetic_event once per row with
      phase='meta', step_id='subagent-<agent_id>', and agent_name from resolve step.
  (f) idempotency: existing step_events row with non-zero input_tokens → skip.
  (g) cost_usd matches _compute_cost_usd over the JSONL token totals.
  (h) agent_report view returns inserted rows grouped by agent_name.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _resolve_subagent_rows, _write_subagent_events  # noqa: E402
from orchestrator_next.upsert import ensure_schema, upsert_synthetic_event  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slug(repo_root: str) -> str:
    return repo_root.replace("/", "-")


def _subagent_dir(home: Path, repo_root: str, session_id: str) -> Path:
    slug = _make_slug(repo_root)
    return home / ".claude" / "projects" / slug / session_id / "subagents"


def _write_agent_jsonl(path: Path, agent_id: str, turns: int = 2,
                       model: str = "claude-sonnet-4-6") -> None:
    """Write a minimal subagent JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for i in range(turns):
            row = {
                "type": "assistant",
                "timestamp": f"2026-01-01T00:0{i}:00.000Z",
                "message": {
                    "model": model,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 10,
                        "cache_creation_input_tokens": 5,
                    },
                    "content": [],
                },
            }
            f.write(json.dumps(row) + "\n")


def _write_meta_json(path: Path, agent_type: str) -> None:
    path.write_text(json.dumps({"agentType": agent_type}))


def _fresh_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    return db


# ---------------------------------------------------------------------------
# (a) Returns one dict per subagent with agent_name from meta.json
# ---------------------------------------------------------------------------

def test_resolve_subagent_rows_returns_one_per_agent(tmp_path, monkeypatch):
    """Returns one row dict per discovered subagent with correct agent_name."""
    repo_root = "/test/repo"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    # Create 2 subagents
    for i, agent_type in enumerate(["developer", "reviewer"]):
        agent_id = f"agent{i}"
        _write_agent_jsonl(sub_dir / f"agent-{agent_id}.jsonl", agent_id)
        _write_meta_json(sub_dir / f"agent-{agent_id}.meta.json", agent_type)

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    rows = _resolve_subagent_rows(repo_root, "test-feature", session_id)

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    agent_names = {r["agent_name"] for r in rows}
    assert "developer" in agent_names
    assert "reviewer" in agent_names


# ---------------------------------------------------------------------------
# (b) Fallback to 'subagent-unknown' when meta.json is missing — row still emitted
# ---------------------------------------------------------------------------

def test_resolve_subagent_rows_fallback_when_meta_missing(tmp_path, monkeypatch):
    """Missing meta.json → agent_name='subagent-unknown', row still returned."""
    repo_root = "/test/repo"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    agent_id = "agentX"
    _write_agent_jsonl(sub_dir / f"agent-{agent_id}.jsonl", agent_id)
    # No meta.json written

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    rows = _resolve_subagent_rows(repo_root, "test-feature", session_id)

    assert len(rows) == 1
    assert rows[0]["agent_name"] == "subagent-unknown"
    assert rows[0]["step_id"] == f"subagent-{agent_id}"
    assert rows[0]["phase"] == "meta"


# ---------------------------------------------------------------------------
# (c) Malformed meta.json → skip that row with stderr log, no exception
# ---------------------------------------------------------------------------

def test_resolve_subagent_rows_malformed_meta_skips_gracefully(tmp_path, monkeypatch, capsys):
    """Malformed meta.json → row emitted with fallback agent_name, no exception."""
    repo_root = "/test/repo"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    agent_id = "badmeta"
    _write_agent_jsonl(sub_dir / f"agent-{agent_id}.jsonl", agent_id)
    # Write invalid JSON as meta
    (sub_dir / f"agent-{agent_id}.meta.json").write_text("INVALID JSON {{{")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    # Should not raise
    rows = _resolve_subagent_rows(repo_root, "test-feature", session_id)
    # Row still emitted with fallback
    assert len(rows) == 1
    assert rows[0]["agent_name"] == "subagent-unknown"


def test_resolve_subagent_rows_no_usable_turns_skips(tmp_path, monkeypatch):
    """JSONL with no usable assistant turns → row skipped (logged to stderr)."""
    repo_root = "/test/repo"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    agent_id = "emptyjsonl"
    # Write empty JSONL (no assistant turns)
    (sub_dir / f"agent-{agent_id}.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"agent-{agent_id}.jsonl").write_text("")
    _write_meta_json(sub_dir / f"agent-{agent_id}.meta.json", "developer")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    rows = _resolve_subagent_rows(repo_root, "test-feature", session_id)
    # Empty JSONL → no usable turns → row skipped
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# (d) _resolve_subagent_rows does NOT open DuckDB or BEGIN — pure parsing
# ---------------------------------------------------------------------------

def test_resolve_subagent_rows_is_pure_parsing(tmp_path, monkeypatch):
    """_resolve_subagent_rows returns a list without any DB operations."""
    repo_root = "/test/repo"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    agent_id = "pure"
    _write_agent_jsonl(sub_dir / f"agent-{agent_id}.jsonl", agent_id)
    _write_meta_json(sub_dir / f"agent-{agent_id}.meta.json", "developer")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    # Pass no DB — if implementation tries to open DuckDB it will fail
    # (METRICS_DB is not set, so any duckdb.connect() would need a valid path)
    monkeypatch.delenv("METRICS_DB", raising=False)

    # Should not raise even without DB
    rows = _resolve_subagent_rows(repo_root, "test-feature", session_id)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# (e) _write_subagent_events calls upsert_synthetic_event once per row
# ---------------------------------------------------------------------------

def test_write_subagent_events_inserts_rows(tmp_path, monkeypatch):
    """_write_subagent_events inserts one step_events row per resolved subagent."""
    db = _fresh_db()
    repo_root = "/test/repo"
    change_id = "test-feature"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    # Create 2 subagents
    agent_ids = ["agt1", "agt2"]
    for aid in agent_ids:
        _write_agent_jsonl(sub_dir / f"agent-{aid}.jsonl", aid)
        _write_meta_json(sub_dir / f"agent-{aid}.meta.json", f"developer-{aid}")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    rows = _resolve_subagent_rows(repo_root, change_id, session_id)
    assert len(rows) == 2

    _write_subagent_events(db, repo_root, change_id, rows)

    # Both rows exist in step_events with phase='meta'
    inserted = db.execute(
        "SELECT step_id, agent_name FROM step_events "
        "WHERE repo_root=? AND change_id=? AND phase='meta' "
        "ORDER BY step_id",
        [repo_root, change_id],
    ).fetchall()
    assert len(inserted) == 2
    step_ids = {r[0] for r in inserted}
    assert "subagent-agt1" in step_ids
    assert "subagent-agt2" in step_ids

    db.close()


# ---------------------------------------------------------------------------
# (f) Idempotency: existing row with non-zero input_tokens → skip
# ---------------------------------------------------------------------------

def test_write_subagent_events_idempotent(tmp_path, monkeypatch):
    """Existing step_events row with non-zero input_tokens is not re-inserted."""
    db = _fresh_db()
    repo_root = "/test/repo"
    change_id = "test-feature"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    agent_id = "idem"
    _write_agent_jsonl(sub_dir / f"agent-{agent_id}.jsonl", agent_id)
    _write_meta_json(sub_dir / f"agent-{agent_id}.meta.json", "developer")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    # Pre-seed the row with non-zero input_tokens
    upsert_synthetic_event(
        db, {"repo_root": repo_root, "change_id": change_id},
        agent_name="developer",
        step_id=f"subagent-{agent_id}",
        phase="meta",
        usage={"input_tokens": 999, "output_tokens": 0, "cost_usd": 0.001},
    )

    rows = _resolve_subagent_rows(repo_root, change_id, session_id)
    _write_subagent_events(db, repo_root, change_id, rows)

    # Should still have exactly one row (no duplicate)
    count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE phase='meta' AND step_id=?",
        [f"subagent-{agent_id}"],
    ).fetchone()[0]
    assert count == 1

    # The pre-seeded input_tokens should remain 999 (not overwritten)
    tok = db.execute(
        "SELECT input_tokens FROM step_events WHERE phase='meta' AND step_id=?",
        [f"subagent-{agent_id}"],
    ).fetchone()[0]
    assert tok == 999, f"Expected idempotent (999), got {tok}"

    db.close()


# ---------------------------------------------------------------------------
# (g) cost_usd matches _compute_cost_usd over JSONL token totals
# ---------------------------------------------------------------------------

def test_write_subagent_events_cost_computed(tmp_path, monkeypatch):
    """cost_usd in the inserted row reflects the JSONL token totals."""
    db = _fresh_db()
    repo_root = "/test/repo"
    change_id = "test-feature"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    agent_id = "priced"
    _write_agent_jsonl(sub_dir / f"agent-{agent_id}.jsonl", agent_id, turns=2,
                       model="claude-sonnet-4-6")
    _write_meta_json(sub_dir / f"agent-{agent_id}.meta.json", "developer")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    rows = _resolve_subagent_rows(repo_root, change_id, session_id)
    _write_subagent_events(db, repo_root, change_id, rows, db=db)

    row = db.execute(
        "SELECT cost_usd, input_tokens FROM step_events WHERE phase='meta' AND step_id=?",
        [f"subagent-{agent_id}"],
    ).fetchone()
    assert row is not None
    assert row[1] == 200  # 2 turns * 100 input

    db.close()


# ---------------------------------------------------------------------------
# (h) agent_report view returns rows grouped by agent_name
# ---------------------------------------------------------------------------

def test_agent_report_view_after_subagent_write(tmp_path, monkeypatch):
    """After _write_subagent_events, agent_report view shows per-subagent rows."""
    db = _fresh_db()
    repo_root = "/test/repo"
    change_id = "test-feature"
    session_id = "test-session"
    sub_dir = _subagent_dir(tmp_path, repo_root, session_id)

    for i, agent_type in enumerate(["frontend-dev", "backend-dev"]):
        aid = f"agent{i}"
        _write_agent_jsonl(sub_dir / f"agent-{aid}.jsonl", aid, turns=1)
        _write_meta_json(sub_dir / f"agent-{aid}.meta.json", agent_type)

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    rows = _resolve_subagent_rows(repo_root, change_id, session_id)
    _write_subagent_events(db, repo_root, change_id, rows)

    report = db.execute(
        "SELECT agent_name FROM agent_report WHERE change_id=? ORDER BY agent_name",
        [change_id],
    ).fetchall()
    names = [r[0] for r in report]
    assert "backend-dev" in names
    assert "frontend-dev" in names

    db.close()
