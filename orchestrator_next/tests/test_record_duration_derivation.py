"""ORC-115: derive usage.duration_ms in record._build_history_entry when absent."""
from __future__ import annotations

from datetime import datetime

from orchestrator_next.record import _build_history_entry


def _call(payload: dict, *, step_id: str = "script-step", phase: str = "main") -> dict:
    return _build_history_entry(
        payload=payload,
        step_id=step_id,
        phase=phase,
        status="completed",
        outputs={},
        agent=None,
        state_raw={"step_history": []},
    )


def test_derives_duration_ms_from_parseable_timestamps():
    started = "2026-07-11T20:00:00Z"
    entry = _call({"started_at": started, "usage": {"input_tokens": 0, "output_tokens": 0}})
    started_dt = datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00"))
    ended_dt = datetime.fromisoformat(entry["ended_at"].replace("Z", "+00:00"))
    expected = int((ended_dt - started_dt).total_seconds() * 1000)
    assert entry["usage"]["duration_ms"] == expected


def test_preserves_payload_duration_ms():
    entry = _call(
        {
            "started_at": "2026-07-11T20:00:00Z",
            "usage": {"duration_ms": 42, "input_tokens": 1, "output_tokens": 2},
        }
    )
    assert entry["usage"]["duration_ms"] == 42


def test_unparseable_started_at_skips_derivation():
    entry = _call({"started_at": "not-a-timestamp", "usage": {}})
    assert "duration_ms" not in entry["usage"]


def test_null_usage_still_gets_duration_ms():
    started = "2026-07-11T20:00:00Z"
    entry = _call({"started_at": started, "usage": None})
    started_dt = datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00"))
    ended_dt = datetime.fromisoformat(entry["ended_at"].replace("Z", "+00:00"))
    expected = int((ended_dt - started_dt).total_seconds() * 1000)
    assert entry["usage"]["duration_ms"] == expected
