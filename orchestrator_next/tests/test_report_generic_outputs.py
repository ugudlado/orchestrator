"""ORC-117 T-4: report.render_report iterates all output keys per step."""
from __future__ import annotations

import io
import sys

import pytest

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


def test_briefing_read_from_outputs():
    """briefing key read from entry["outputs"]["briefing"]."""
    history = [_entry("foo", outputs={"briefing": "hello"})]
    _, stderr = _render(history)
    assert "hello" in stderr


def test_briefing_fallback_to_legacy_top_level():
    """top-level entry["briefing"] still read for legacy state files without outputs."""
    history = [_entry("foo", briefing="legacy")]
    _, stderr = _render(history)
    assert "legacy" in stderr


def test_novel_output_key_rendered():
    """A novel output key surfaces in the rendered report."""
    history = [_entry("foo", outputs={"briefing": "b", "custom_metric": "42"})]
    _, stderr = _render(history)
    assert "custom_metric" in stderr
    assert "42" in stderr


def test_long_value_truncated():
    """Long output values are truncated in the report."""
    history = [_entry("foo", outputs={"blob": "x" * 500})]
    _, stderr = _render(history)
    # The printed line for blob should not contain the full 500-char value
    for line in stderr.splitlines():
        if "blob" in line:
            assert len(line) < 300, f"line not truncated: {line[:100]!r}"
            break
