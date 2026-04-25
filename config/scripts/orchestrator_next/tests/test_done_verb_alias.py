"""T-13 (RED) / T-14 (GREEN): done verb dispatch via bin/orchestrator.

Tests cover FR-1 — both verbs must route to record_main during the Stage A alias period.

Cases:
  (a) `bin/orchestrator done state.yaml <<<{}` invokes record_main (same error as record for missing file)
  (b) `bin/orchestrator record state.yaml <<<{}` continues to invoke record_main (backward compat)
  (c) usage banner (no args) mentions both verbs in Stage A

Expected RED state at T-13:
  - (a) FAILS — 'done' is not yet a recognized verb; exits 3 with Usage banner, NOT record_main error.
  - (b) PASSES — 'record' already dispatches to record_main; this is a compat assertion.
  - (c) FAILS — banner only mentions 'record', not 'done'.

These turn GREEN in T-14 (add 'done' to verb dispatch + update banner).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")


def _run_orchestrator(
    args: list[str],
    stdin_text: str = "{}",
) -> subprocess.CompletedProcess:
    """Run bin/orchestrator with given args, capturing stdout + stderr."""
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR] + args,
        input=stdin_text,
        capture_output=True,
        text=True,
    )


class TestDoneVerbDispatch(unittest.TestCase):
    """Verify that 'done' verb routes to record_main (same behavior as 'record')."""

    def test_done_verb_invokes_record_main_on_missing_file(self) -> None:
        """(a) `bin/orchestrator done <missing.yaml>` invokes record_main.

        record_main exits non-zero when state.yaml is missing, with a specific error message
        about the missing file (NOT the generic 'Usage:' banner from unrecognised verbs).
        This distinguishes dispatch-to-record_main from dispatch-to-usage.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = os.path.join(tmpdir, "nonexistent.yaml")
            result = _run_orchestrator(["done", missing_path], stdin_text="{}")

        # Must exit non-zero (record_main exits non-zero for missing file)
        self.assertNotEqual(
            result.returncode,
            0,
            "Expected non-zero exit for missing state.yaml via 'done' verb.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )
        # Must NOT output the generic Usage banner (that's what happens for unrecognised verbs)
        # Unrecognised verbs say "Usage:" and exit 3 immediately. record_main errors differently.
        combined = result.stdout + result.stderr
        # record_main says "state.yaml not found" or "error: ... not found" or similar
        # The key signal: the error is about the file, not an unrecognised-verb Usage banner
        # that lists ALL verbs. We check by confirming 'ingest-driver' is NOT in the output
        # (the Usage banner lists ingest-driver; record_main errors do not).
        self.assertNotIn(
            "ingest-driver",
            combined,
            "Stderr should not contain 'ingest-driver' (that would mean the Usage banner was "
            "shown, indicating 'done' was not recognised as a verb).\n"
            f"stderr: {result.stderr!r}",
        )

    def test_record_verb_still_invokes_record_main(self) -> None:
        """(b) `bin/orchestrator record <missing.yaml>` continues to invoke record_main.

        Backward compat: 'record' must not regress during Stage A alias period.
        Uses the same signal as test (a): the error is about the file, not an
        unrecognised-verb Usage banner listing ingest-driver.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = os.path.join(tmpdir, "nonexistent.yaml")
            result = _run_orchestrator(["record", missing_path], stdin_text="{}")

        self.assertNotEqual(
            result.returncode,
            0,
            "Expected non-zero exit for missing state.yaml via 'record' verb.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "ingest-driver",
            combined,
            "Stderr should not contain 'ingest-driver' (that means 'record' was not recognised).\n"
            f"stderr: {result.stderr!r}",
        )

    def test_done_and_record_produce_identical_output_for_missing_file(self) -> None:
        """(a)+(b) Golden diff: 'done' and 'record' produce same output for same missing input.

        Verifies that the alias routes to the exact same code path, not a parallel copy.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = os.path.join(tmpdir, "nonexistent.yaml")
            result_done = _run_orchestrator(["done", missing_path], stdin_text="{}")
            result_record = _run_orchestrator(["record", missing_path], stdin_text="{}")

        # Exit codes must match
        self.assertEqual(
            result_done.returncode,
            result_record.returncode,
            f"Exit codes differ: done={result_done.returncode}, record={result_record.returncode}",
        )
        # Stderr must match (both call the same record_main, so same error messages)
        self.assertEqual(
            result_done.stderr,
            result_record.stderr,
            f"Stderr differs between 'done' and 'record':\n"
            f"  done:   {result_done.stderr!r}\n"
            f"  record: {result_record.stderr!r}",
        )


def _get_banner() -> str:
    """Run bin/orchestrator with no args and return stderr (the usage banner)."""
    return _run_orchestrator([]).stderr


class TestUsageBannerMentionsDone(unittest.TestCase):
    """Verify that the usage banner mentions 'done' (Stage A+C requirement)."""

    def test_banner_mentions_done_verb(self) -> None:
        """(c) Usage banner includes 'orchestrator done'."""
        banner = _get_banner()
        self.assertIn(
            "orchestrator done",
            banner,
            f"Expected 'orchestrator done' in usage banner.\nBanner: {banner!r}",
        )


class TestStageCDeprecation(unittest.TestCase):
    """T-24 (RED) / T-25 (GREEN): Stage C tests.

    Stage C requirements (FR-12):
    - Banner mentions only 'done', not 'record', 'ingest-driver', or 'ingest-subagents'.
    - 'record' verb still routes silently (backward compat, undocumented).
    - 'ingest-driver' and 'ingest-subagents' are fully removed verbs (return non-zero).
    """

    def test_stage_c_banner(self) -> None:
        """(a) Stage C usage banner does NOT mention 'ingest-driver', 'ingest-subagents',
        or 'orchestrator record'.

        FR-12: After Stage C, the banner shows only 'done'. 'record' is kept for
        silent routing but must not appear in the usage banner.
        """
        banner = _get_banner()
        self.assertNotIn(
            "ingest-driver",
            banner,
            f"Stage C banner must NOT mention 'ingest-driver'.\nBanner: {banner!r}",
        )
        self.assertNotIn(
            "ingest-subagents",
            banner,
            f"Stage C banner must NOT mention 'ingest-subagents'.\nBanner: {banner!r}",
        )
        self.assertNotIn(
            "orchestrator record",
            banner,
            f"Stage C banner must NOT mention 'orchestrator record'.\nBanner: {banner!r}",
        )

    def test_record_silent_routing(self) -> None:
        """(b) 'record' verb still routes to record_main after Stage C banner cleanup.

        FR-12 + NFR-1: 'record' stays in the accepted-verb tuple for one cycle as a
        silent fallback. It must NOT show the Usage banner (unrecognised-verb response).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = os.path.join(tmpdir, "nonexistent.yaml")
            result = _run_orchestrator(["record", missing_path], stdin_text="{}")

        # Must exit non-zero (record_main errors on missing file, not the Usage banner)
        self.assertNotEqual(
            result.returncode,
            0,
            "Expected non-zero exit for missing state.yaml via 'record' verb.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )
        # record_main errors about the file; the Usage banner (unrecognised verb) would NOT
        # mention 'ingest-driver' once Stage C removes it from the banner — but we verify
        # routing by checking the error is about the file, not an unrecognised-verb exit-3.
        # The key signal: exit code must come from record_main (file-not-found), not from
        # the verb-unrecognised path.
        # We test this by confirming 'done' dispatch still works identically to 'record'.
        with tempfile.TemporaryDirectory() as tmpdir2:
            missing_path2 = os.path.join(tmpdir2, "nonexistent.yaml")
            result_done = _run_orchestrator(["done", missing_path2], stdin_text="{}")
            result_record = _run_orchestrator(["record", missing_path2], stdin_text="{}")

        self.assertEqual(
            result_done.returncode,
            result_record.returncode,
            "After Stage C, 'record' must route identically to 'done' (same exit code).\n"
            f"done exit: {result_done.returncode}, record exit: {result_record.returncode}",
        )
        self.assertEqual(
            result_done.stderr,
            result_record.stderr,
            "After Stage C, 'record' must route identically to 'done' (same stderr).\n"
            f"done stderr: {result_done.stderr!r}\nrecord stderr: {result_record.stderr!r}",
        )

    def test_ingest_verbs_removed(self) -> None:
        """(c) 'ingest-driver' returns non-zero after Stage C verb-tuple cleanup.

        FR-8: 'ingest-driver' and 'ingest-subagents' are removed from the accepted-verb
        tuple. Calling them must produce an unrecognised-verb response (Usage banner,
        exit code 3), NOT dispatch to the (now-deleted) _ingest_driver_main function.
        """
        # Test ingest-driver
        result_driver = _run_orchestrator(["ingest-driver", "--change-id", "test", "--session-id", "abc"])
        self.assertNotEqual(
            result_driver.returncode,
            0,
            "Expected non-zero exit for 'ingest-driver' after Stage C removal.\n"
            f"stdout: {result_driver.stdout!r}\nstderr: {result_driver.stderr!r}",
        )
        # The Usage banner is printed for unrecognised verbs — confirm it shows 'done'
        combined_driver = result_driver.stdout + result_driver.stderr
        self.assertIn(
            "orchestrator done",
            combined_driver,
            "Expected Usage banner (mentioning 'orchestrator done') for removed verb 'ingest-driver'.\n"
            f"output: {combined_driver!r}",
        )

        # Test ingest-subagents
        result_subagents = _run_orchestrator(["ingest-subagents", "--change-id", "test", "--session-id", "abc"])
        self.assertNotEqual(
            result_subagents.returncode,
            0,
            "Expected non-zero exit for 'ingest-subagents' after Stage C removal.\n"
            f"stdout: {result_subagents.stdout!r}\nstderr: {result_subagents.stderr!r}",
        )
        combined_subagents = result_subagents.stdout + result_subagents.stderr
        self.assertIn(
            "orchestrator done",
            combined_subagents,
            "Expected Usage banner (mentioning 'orchestrator done') for removed verb 'ingest-subagents'.\n"
            f"output: {combined_subagents!r}",
        )
