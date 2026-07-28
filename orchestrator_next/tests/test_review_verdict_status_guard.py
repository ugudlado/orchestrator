"""review verdict→status guard — regression for the BKG-575 gate bypass.

A live run's review step emitted status: completed with
phase_review_report.verdict: needs_work; routing keys off status, so the
on_failure edge never fired and the workflow advanced past a failing review.
Mirror of the design-review guard: coerce at the record boundary, reject
what coercion can't fix.
"""
from __future__ import annotations

import pytest

from orchestrator_next.record import (
    _RecordError,
    _normalize_review_payload_status,
    _validate_phase_review_output,
)


def _outputs(verdict: str) -> dict:
    return {"phase_review_report": {"verdict": verdict}}


class TestReviewVerdictStatusGuard:
    def test_completed_needs_work_coerced_to_failed(self):
        assert (
            _normalize_review_payload_status("review", "completed", _outputs("needs_work"))
            == "failed"
        )

    def test_completed_incomplete_phase_coerced_to_failed(self):
        assert (
            _normalize_review_payload_status("review", "completed", _outputs("incomplete_phase"))
            == "failed"
        )

    def test_completed_pass_untouched(self):
        assert (
            _normalize_review_payload_status("review", "completed", _outputs("pass"))
            == "completed"
        )

    def test_failed_needs_work_untouched(self):
        assert (
            _normalize_review_payload_status("review", "failed", _outputs("needs_work"))
            == "failed"
        )

    def test_missing_report_untouched(self):
        assert _normalize_review_payload_status("review", "completed", {}) == "completed"

    def test_validate_rejects_completed_non_pass_verdict(self):
        with pytest.raises(_RecordError) as exc:
            _validate_phase_review_output("review", _outputs("needs_work"))
        assert exc.value.reason["reason"] == "invalid_phase_review_status_for_verdict"

    def test_validate_accepts_completed_pass(self):
        assert _validate_phase_review_output("review", _outputs("pass")) is None
