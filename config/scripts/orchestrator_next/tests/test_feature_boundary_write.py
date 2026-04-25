"""T-7 (RED) / T-8 (GREEN): _resolve_driver_session + _write_driver_session.

Tests cover FR-6 — session-id resolution must reproduce the legacy
_ingest_driver_main behavior.

Cases:
  (a) env var $ORCHESTRATOR_DRIVER_SESSION_ID is honored
  (b) JSONL fallback finds the most recent file by mtime
  (c) raises RuntimeError when neither env var nor JSONL resolves
  (d) returned cost_usd matches _compute_cost_usd over the JSONL token totals
  (e) _write_driver_session inserts a row into driver_sessions
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _resolve_driver_session, _write_driver_session  # noqa: E402
from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# JSONL fixture helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, turns: int = 2, model: str = "claude-sonnet-4-6") -> None:
    """Write a minimal JSONL file with `turns` assistant turns."""
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


def _make_slug(repo_root: str) -> str:
    return repo_root.replace("/", "-")


def _projects_dir(home: Path) -> Path:
    return home / ".claude" / "projects"


# ---------------------------------------------------------------------------
# (a) env var $ORCHESTRATOR_DRIVER_SESSION_ID is honored
# ---------------------------------------------------------------------------

def test_resolve_uses_env_var_session_id(tmp_path, monkeypatch):
    """ORCHESTRATOR_DRIVER_SESSION_ID env var → session_id resolved from env, JSONL read."""
    session_id = "test-session-abc"
    repo_root = "/test/repo"
    slug = _make_slug(repo_root)

    # Create the JSONL file at the expected location
    jsonl_path = _projects_dir(tmp_path) / slug / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, turns=2)

    monkeypatch.setenv("ORCHESTRATOR_DRIVER_SESSION_ID", session_id)
    # Redirect Path.home() to tmp_path
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    state = {"repo_root": repo_root, "change_id": "test-feature"}
    result = _resolve_driver_session(state, "test-feature")

    assert result["session_id"] == session_id, (
        f"Expected session_id={session_id!r}, got {result['session_id']!r}"
    )
    assert result["input_tokens"] == 200  # 2 turns * 100
    assert result["output_tokens"] == 100  # 2 turns * 50


# ---------------------------------------------------------------------------
# (b) JSONL fallback finds the most recent file by mtime
# ---------------------------------------------------------------------------

def test_resolve_falls_back_to_most_recent_jsonl(tmp_path, monkeypatch):
    """When env var absent, resolves session_id from most recent JSONL by mtime."""
    repo_root = "/test/repo"
    slug = _make_slug(repo_root)
    proj_dir = _projects_dir(tmp_path) / slug
    proj_dir.mkdir(parents=True)

    # Write two JSONL files with different mtimes
    older = proj_dir / "older-session.jsonl"
    newer = proj_dir / "newer-session.jsonl"
    _write_jsonl(older, turns=1)
    time.sleep(0.05)  # ensure mtime difference
    _write_jsonl(newer, turns=3)

    monkeypatch.delenv("ORCHESTRATOR_DRIVER_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    state = {"repo_root": repo_root, "change_id": "test-feature"}
    result = _resolve_driver_session(state, "test-feature")

    assert result["session_id"] == "newer-session", (
        f"Expected newer-session, got {result['session_id']!r}"
    )
    assert result["input_tokens"] == 300  # 3 turns * 100


# ---------------------------------------------------------------------------
# (c) raises when neither env var nor JSONL resolves
# ---------------------------------------------------------------------------

def test_resolve_raises_when_no_session_id(tmp_path, monkeypatch):
    """No env var and no JSONL files → RuntimeError."""
    repo_root = "/test/repo"
    slug = _make_slug(repo_root)
    # Create empty project dir (no JSONL files)
    (_projects_dir(tmp_path) / slug).mkdir(parents=True)

    monkeypatch.delenv("ORCHESTRATOR_DRIVER_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    state = {"repo_root": repo_root, "change_id": "test-feature"}
    with pytest.raises(RuntimeError, match="session_id"):
        _resolve_driver_session(state, "test-feature")


def test_resolve_raises_when_project_dir_missing(tmp_path, monkeypatch):
    """Project dir does not exist → RuntimeError."""
    monkeypatch.delenv("ORCHESTRATOR_DRIVER_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    state = {"repo_root": "/no/such/repo", "change_id": "test-feature"}
    with pytest.raises(RuntimeError):
        _resolve_driver_session(state, "test-feature")


# ---------------------------------------------------------------------------
# (d) cost_usd matches _compute_cost_usd over JSONL token totals
# ---------------------------------------------------------------------------

def test_resolve_cost_usd_from_jsonl(tmp_path, monkeypatch):
    """cost_usd is populated when a pricing DB is available."""
    session_id = "priced-session"
    repo_root = "/test/repo"
    slug = _make_slug(repo_root)

    jsonl_path = _projects_dir(tmp_path) / slug / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, turns=2, model="claude-sonnet-4-6")

    monkeypatch.setenv("ORCHESTRATOR_DRIVER_SESSION_ID", session_id)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    # Open a real DB with pricing seeded
    db = duckdb.connect(":memory:")
    ensure_schema(db)  # runs seed migration

    state = {"repo_root": repo_root, "change_id": "test-feature"}
    result = _resolve_driver_session(state, "test-feature", db=db)
    db.close()

    # 2 turns * 100 input + 50 output + 10 cache_read + 5 cache_creation
    # at claude-sonnet-4-6 rates: input=3.00, output=15.00, cache_read=0.30, cache_creation=3.75 (per MTok)
    # cost = (200*3 + 100*15 + 20*0.30 + 10*3.75) / 1_000_000
    expected_cost = (200 * 3.0 + 100 * 15.0 + 20 * 0.30 + 10 * 3.75) / 1_000_000
    assert result.get("cost_usd") is not None, "cost_usd should be computed when DB is present"
    assert abs(result["cost_usd"] - expected_cost) < 0.00001, (
        f"cost_usd mismatch: expected ~{expected_cost:.8f}, got {result['cost_usd']}"
    )


# ---------------------------------------------------------------------------
# (e) _write_driver_session inserts a row into driver_sessions
# ---------------------------------------------------------------------------

def test_write_driver_session_inserts_row():
    """_write_driver_session inserts one row into driver_sessions."""
    db = duckdb.connect(":memory:")
    ensure_schema(db)

    session = {
        "session_id": "sess-abc",
        "model": "claude-sonnet-4-6",
        "total_tokens": 300,
        "input_tokens": 200,
        "output_tokens": 100,
        "cost_usd": 0.005,
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T01:00:00Z",
    }

    _write_driver_session(db, "/test/repo", "test-feature", session)

    rows = db.execute(
        "SELECT session_id, input_tokens, cost_usd FROM driver_sessions "
        "WHERE change_id='test-feature'",
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "sess-abc"
    assert rows[0][1] == 200
    assert abs(rows[0][2] - 0.005) < 0.00001

    db.close()
