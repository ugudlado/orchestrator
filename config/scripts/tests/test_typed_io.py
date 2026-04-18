"""HL-287 M1 tests: typed inputs / expected_outputs in the dispatch action.

Covers:
  1. A contract with declared `inputs:` resolves values from a prior step's
     ``evidence.outputs.<name>`` and from state.yaml top-level keys.
  2. A contract with no declared `inputs:` produces ``inputs: {}``.
  3. Missing inputs do NOT block at M1 — the dispatcher threads what it can
     (strict validation is M2's scope).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")


def _run_next(fixture_name: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["METRICS_DB"] = "/tmp/test-typed-io.duckdb"
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "next",
         os.path.join(_FIXTURES_DIR, fixture_name)],
        capture_output=True, text=True, env=env,
    )


class TestTypedIO(unittest.TestCase):
    """HL-287 M1: typed inputs / expected_outputs in action dict."""

    def test_inputs_resolved_from_prior_step_and_state_top_level(self):
        """A contract with declared inputs resolves values from prior step
        evidence.outputs.<name> (`prior_output`) and state.yaml top-level
        (`slug`). `expected_outputs` carries the contract's declared outputs."""
        result = _run_next("state-typed-io-resolved.yaml")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        action = json.loads(result.stdout)
        self.assertEqual(action["action"], "run_inline")
        self.assertEqual(action["step_id"], "step-typed-io")
        self.assertEqual(
            action["inputs"],
            {"slug": "hl-287", "prior_output": "value-from-producer"},
        )
        self.assertEqual(
            action["expected_outputs"],
            ["computed_value", "derived_flag"],
        )

    def test_no_declared_inputs_produces_empty_dict(self):
        """A contract without `inputs:` still surfaces `inputs: {}`
        in the action. Backward-compatible default."""
        result = _run_next("state-pending-inline.yaml")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        action = json.loads(result.stdout)
        # step-inline-only fixture has no `inputs:` field.
        self.assertEqual(action["inputs"], {})
        # It does declare outputs: [result].
        self.assertEqual(action["expected_outputs"], ["result"])

    def test_missing_inputs_not_blocking_at_m1(self):
        """At M1, missing inputs thread through as an incomplete dict but do
        NOT block the dispatcher. Strict validation is M2's exit criterion."""
        result = _run_next("state-typed-io-missing.yaml")
        # Step contract declares inputs [slug, prior_output]; fixture provides
        # neither. Expect run_inline anyway with inputs: {} (missing values
        # silently dropped).
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        action = json.loads(result.stdout)
        self.assertEqual(action["action"], "run_inline")
        self.assertEqual(action["inputs"], {})
        self.assertEqual(
            action["expected_outputs"],
            ["computed_value", "derived_flag"],
        )


if __name__ == "__main__":
    unittest.main()
