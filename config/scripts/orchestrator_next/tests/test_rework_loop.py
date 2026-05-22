"""orc-67: run-phase-review needs_work rework loop.

T-2 (RED): unit tests for the three pure helpers — `_payload_phase_review_verdict`,
`_rework_loop_active`, `_max_retry_rounds`. They fail today because the symbols
do not exist in record.py yet.

Note: `record.py` already has an entry-time `_phase_review_verdict(entry)` used
by `extract_review_scores` (reads `entry.evidence.outputs`). The rework loop
needs a *payload-time* extractor (reads `payload.outputs` directly and gates on
`step_id`), so it is named distinctly to avoid breaking that existing helper.

T-4 (RED) extends this module with end-to-end `record()` cases for node
re-open and ceiling escalation.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next import record  # noqa: E402


# ---------------------------------------------------------------------------
# _payload_phase_review_verdict
# ---------------------------------------------------------------------------

class TestPayloadPhaseReviewVerdict:

    def _payload(self, step_id: str, report) -> dict:
        outputs = {} if report is None else {"phase_review_report": report}
        return {"step_id": step_id, "phase": "implement", "outputs": outputs}

    def test_returns_verdict_for_run_phase_review_payload(self):
        payload = self._payload("run-phase-review", {"verdict": "needs_work"})
        assert record._payload_phase_review_verdict(payload) == "needs_work"

    def test_returns_pass_verdict(self):
        payload = self._payload("run-phase-review", {"verdict": "pass"})
        assert record._payload_phase_review_verdict(payload) == "pass"

    def test_returns_none_for_other_step_ids(self):
        payload = self._payload("execute-next-task", {"verdict": "needs_work"})
        assert record._payload_phase_review_verdict(payload) is None

    def test_returns_none_when_report_absent(self):
        payload = self._payload("run-phase-review", None)
        assert record._payload_phase_review_verdict(payload) is None

    def test_returns_none_when_report_not_a_dict(self):
        payload = self._payload("run-phase-review", "not-a-dict")
        assert record._payload_phase_review_verdict(payload) is None

    def test_returns_none_when_outputs_missing(self):
        payload = {"step_id": "run-phase-review", "phase": "implement"}
        assert record._payload_phase_review_verdict(payload) is None


# ---------------------------------------------------------------------------
# _rework_loop_active
# ---------------------------------------------------------------------------

class TestReworkLoopActive:

    def test_retry_for_needs_work_below_ceiling(self):
        assert record._rework_loop_active(
            "needs_work", {"run-phase-review": 2}, 8
        ) == "retry"

    def test_retry_for_incomplete_phase_below_ceiling(self):
        assert record._rework_loop_active(
            "incomplete_phase", {"run-phase-review": 0}, 8
        ) == "retry"

    def test_escalate_at_ceiling(self):
        assert record._rework_loop_active(
            "needs_work", {"run-phase-review": 8}, 8
        ) == "escalate"

    def test_escalate_above_ceiling(self):
        assert record._rework_loop_active(
            "needs_work", {"run-phase-review": 9}, 8
        ) == "escalate"

    def test_none_for_pass_verdict(self):
        assert record._rework_loop_active(
            "pass", {"run-phase-review": 2}, 8
        ) is None

    def test_none_for_unknown_verdict(self):
        assert record._rework_loop_active(
            "something-else", {"run-phase-review": 2}, 8
        ) is None

    def test_retries_absent_treated_as_zero(self):
        # retries dict has no run-phase-review key → count 0 → retry
        assert record._rework_loop_active("needs_work", {}, 8) == "retry"

    def test_retries_none_treated_as_zero(self):
        # retries is None → count 0, no raise
        assert record._rework_loop_active("needs_work", None, 8) == "retry"

    def test_retries_not_a_dict_treated_as_zero(self):
        # retries is a malformed non-dict → count 0, no raise
        assert record._rework_loop_active("needs_work", ["bad"], 8) == "retry"


# ---------------------------------------------------------------------------
# _max_retry_rounds
# ---------------------------------------------------------------------------

class TestMaxRetryRounds:

    def _write_project_yaml(self, root, content) -> None:
        spec_dir = root / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "project.yaml").write_text(yaml.safe_dump(content))

    def test_reads_max_retry_rounds_from_project_yaml(self, tmp_path):
        self._write_project_yaml(tmp_path, {"quality_bar": {"max_retry_rounds": 8}})
        state_raw = {"repo_root": str(tmp_path)}
        assert record._max_retry_rounds(state_raw) == 8

    def test_reads_from_worktree_path_when_present(self, tmp_path):
        self._write_project_yaml(tmp_path, {"quality_bar": {"max_retry_rounds": 5}})
        state_raw = {"worktree_path": str(tmp_path), "repo_root": "/nonexistent"}
        assert record._max_retry_rounds(state_raw) == 5

    def test_default_3_with_warning_when_key_absent(self, tmp_path, capsys):
        self._write_project_yaml(tmp_path, {"quality_bar": {}})
        state_raw = {"repo_root": str(tmp_path)}
        assert record._max_retry_rounds(state_raw) == 3
        assert "[record]" in capsys.readouterr().err

    def test_default_3_with_warning_when_project_yaml_missing(self, tmp_path, capsys):
        # no project.yaml written at all
        state_raw = {"repo_root": str(tmp_path)}
        assert record._max_retry_rounds(state_raw) == 3
        assert "[record]" in capsys.readouterr().err
