"""ORC-117 T-4: report.render_report iterates all output keys per step."""
from __future__ import annotations

import io
import sys

from orchestrator_next import report as report_mod


def _render(step_history: list) -> tuple[dict, str]:
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        result = report_mod.render_report(step_history, [])
    finally:
        sys.stderr = old
    return result, buf.getvalue()


def _entry(step_id="foo", outputs=None, **kwargs):
    base = {
        "step_id": step_id,
        "status": "completed",
        "attempt": 1,
        "usage": {},
    }
    if outputs is not None:
        base["outputs"] = outputs
    base.update(kwargs)
    return base


def test_reason_read_from_outputs():
    history = [_entry("foo", outputs={"reason": "hello"})]
    _, stderr = _render(history)
    assert "hello" in stderr


def test_legacy_briefing_not_read():
    history = [_entry("foo", outputs={"briefing": "legacy"}, briefing="top")]
    _, stderr = _render(history)
    assert "legacy" not in stderr
    assert "top" not in stderr


def test_novel_output_key_rendered():
    history = [_entry("foo", outputs={"reason": "b", "custom_metric": "42"})]
    _, stderr = _render(history)
    assert "custom_metric" in stderr
    assert "42" in stderr


def test_long_value_truncated():
    history = [_entry("foo", outputs={"reason": "ok", "blob": "x" * 500})]
    _, stderr = _render(history)
    for line in stderr.splitlines():
        if "blob" in line:
            assert len(line) < 300, f"line not truncated: {line[:100]!r}"
            break
