"""
T-10 (RED): Retired-CLI regression tests — `orchestrator cost` and `orchestrator metrics`
verbs must return exit code 3 after T-11 removes them.

Scenarios:
  (a) `bin/orchestrator cost --change-id foo` exits 3, stderr contains "Usage:" and does
      NOT contain the word "cost" (i.e. the verb is unrecognised, not dispatched).
  (b) `bin/orchestrator metrics --change-id foo` exits 3 similarly — stderr contains
      "Usage:" and does NOT contain "metrics".
  (c) Grep assertion: `rg -l 'orchestrator (cost|metrics)' bin/ config/scripts/ scripts/
      skills/ --glob '!**/archive/**' --glob '!**/backlog.md'`
      returns zero matches — no production code references the retired verbs.

Expected RED state at T-10:
  - (a) FAILS — `bin/orchestrator cost` currently exits 0 (the verb is dispatched).
  - (b) FAILS — `bin/orchestrator metrics` currently exits 0 (the verb is dispatched).
  - (c) FAILS — rg finds matches in bin/orchestrator and config/scripts/tests/test_cost_cli.py.
  These turn GREEN in T-11 (verb deletion) and T-12 (test_cost_cli.py deletion).
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN_ORCHESTRATOR = os.path.join(os.path.expanduser("~"), ".local", "bin", "orchestrator")


def _run_orchestrator(args: list[str]) -> subprocess.CompletedProcess:
    """Run bin/orchestrator with given args, capturing stdout + stderr."""
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR] + args,
        capture_output=True,
        text=True,
    )


class TestRetiredCLIVerbs(unittest.TestCase):
    """Exit-code and stderr assertions for retired `cost` and `metrics` verbs."""

    def test_cost_verb_exits_3(self) -> None:
        """(a) `orchestrator cost --change-id foo` exits 3 after verb removal."""
        result = _run_orchestrator(["cost", "--change-id", "foo"])
        self.assertEqual(
            result.returncode,
            3,
            f"Expected exit 3 for retired 'cost' verb, got {result.returncode}.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )

    def test_cost_verb_stderr_contains_usage(self) -> None:
        """(a) `orchestrator cost` stderr contains 'Usage:' (unrecognised-verb message)."""
        result = _run_orchestrator(["cost", "--change-id", "foo"])
        self.assertIn(
            "Usage:",
            result.stderr,
            f"Expected 'Usage:' in stderr for retired 'cost' verb.\n"
            f"stderr: {result.stderr!r}",
        )

    def test_cost_verb_stderr_does_not_contain_cost(self) -> None:
        """(a) `orchestrator cost` stderr must NOT contain the word 'cost' (not dispatched)."""
        result = _run_orchestrator(["cost", "--change-id", "foo"])
        self.assertNotIn(
            "cost",
            result.stderr.lower(),
            f"Stderr should not mention 'cost' (verb must be unrecognised, not dispatched).\n"
            f"stderr: {result.stderr!r}",
        )

    def test_metrics_verb_exits_3(self) -> None:
        """(b) `orchestrator metrics --change-id foo` exits 3 after verb removal."""
        result = _run_orchestrator(["metrics", "--change-id", "foo"])
        self.assertEqual(
            result.returncode,
            3,
            f"Expected exit 3 for retired 'metrics' verb, got {result.returncode}.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )

    def test_metrics_verb_stderr_contains_usage(self) -> None:
        """(b) `orchestrator metrics` stderr contains 'Usage:' (unrecognised-verb message)."""
        result = _run_orchestrator(["metrics", "--change-id", "foo"])
        self.assertIn(
            "Usage:",
            result.stderr,
            f"Expected 'Usage:' in stderr for retired 'metrics' verb.\n"
            f"stderr: {result.stderr!r}",
        )

    def test_metrics_verb_stderr_does_not_contain_metrics(self) -> None:
        """(b) `orchestrator metrics` stderr must NOT contain 'metrics' (not dispatched)."""
        result = _run_orchestrator(["metrics", "--change-id", "foo"])
        self.assertNotIn(
            "metrics",
            result.stderr.lower(),
            f"Stderr should not mention 'metrics' (verb must be unrecognised, not dispatched).\n"
            f"stderr: {result.stderr!r}",
        )


class TestNoProductionReferencesToRetiredVerbs(unittest.TestCase):
    """(c) Static analysis: zero matches for 'orchestrator (cost|metrics)' in production code."""

    def test_no_orchestrator_cost_or_metrics_references(self) -> None:
        """rg finds zero matches for 'orchestrator (cost|metrics)' in production directories.

        Scans: bin/, orchestrator_next/, config/steps/, skills/
        Excludes: archive/, backlog.md, and test directories.

        Test directories (tests/, __tests__/) are excluded because:
        - test_retired_cli.py (this file) legitimately documents the retired verbs in
          docstrings and string literals — that's its purpose.
        - test_cost_cli.py (deleted in T-12) tests the verb directly — excluded here
          and removed at source in T-12.

        AC-7 intent: no *production code* references the retired verbs after T-11 + T-12.

        Expected to FAIL at T-10 (matches exist in bin/orchestrator). Turns GREEN
        after T-11 removes the verb dispatch code.
        """
        result = subprocess.run(
            [
                "rg",
                "-l",
                "orchestrator (cost|metrics)",
                "bin/",
                "orchestrator_next/",
                "config/steps/",
                "skills/",
                "--glob",
                "!**/archive/**",
                "--glob",
                "!**/backlog.md",
                "--glob",
                "!**/tests/**",
                "--glob",
                "!**/__tests__/**",
            ],
            capture_output=True,
            text=True,
            cwd=_WORKTREE_ROOT,
        )
        # rg exits 1 when no matches found (that's the GREEN state we want)
        # rg exits 0 when matches are found (that's the RED/failing state)
        matching_files = result.stdout.strip()
        self.assertEqual(
            result.returncode,
            1,
            f"Expected rg to find zero matches in production code (exit 1), but found:\n"
            f"{matching_files}\n"
            f"Remove these references in T-11 (bin/orchestrator dispatch code).",
        )
