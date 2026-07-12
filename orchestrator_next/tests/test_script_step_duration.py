"""Script steps must record a real wall-clock duration.

record.py derives usage.duration_ms from ended_at - started_at, but that path
is unusable for script steps: run_script_step sent no started_at (so record
defaulted it to now == ended_at => 0ms), and _utcnow_iso() truncates to whole
seconds anyway, which would floor any sub-second step to 0.

So run_script_step measures elapsed time itself and passes duration_ms in the
payload. record.py already prefers a supplied duration_ms over deriving one.
"""
from __future__ import annotations

import datetime as _dt

from orchestrator_next.record import _build_history_entry


def _entry(payload: dict) -> dict:
    return _build_history_entry(
        payload=payload,
        step_id=payload["step_id"],
        phase="main",
        status=payload["status"],
        outputs={},
        agent=None,
        state_raw={},
    )


def test_supplied_duration_ms_is_preserved():
    """A script step that measured its own elapsed time keeps that number."""
    entry = _entry({
        "step_id": "create-worktree",
        "status": "completed",
        "usage": {"duration_ms": 1500},
    })
    assert entry["usage"]["duration_ms"] == 1500


def test_sub_second_duration_survives():
    """Truncated ISO stamps would floor this to 0; an explicit value must not."""
    entry = _entry({
        "step_id": "ticket-start",
        "status": "completed",
        "usage": {"duration_ms": 240},
    })
    assert entry["usage"]["duration_ms"] == 240


def test_derivation_still_covers_payloads_without_duration():
    """Agent steps / old payloads keep the derive-from-timestamps fallback."""
    started = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=5)
    entry = _entry({
        "step_id": "explore",
        "status": "completed",
        "started_at": started.isoformat(),
    })
    assert entry["usage"]["duration_ms"] >= 4000
