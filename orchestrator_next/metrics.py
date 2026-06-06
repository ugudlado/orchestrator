"""Step metrics: duration, tokens, and cost."""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wall_clock_minutes(state: dict) -> float | None:
    """Compute wall clock in minutes from state started_at and completed_at.

    Returns None if either timestamp is missing or unparseable.
    """
    started_at = state.get("started_at")
    completed_at = state.get("completed_at")
    if not started_at or not completed_at:
        return None

    def _parse_ts(ts: Any) -> _dt.datetime | None:
        if isinstance(ts, _dt.datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=_dt.timezone.utc)
            return ts
        s = str(ts).strip().replace(" ", "T")
        s = re.sub(r"\+00:00$", "Z", s)
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = _dt.datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
                return parsed.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                continue
        return None

    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60.0, 4)
