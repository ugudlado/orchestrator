"""
End-to-end tests for estimated_cost_so_far in `orchestrator next` output.

Covers:
  AC-1: Two terminal entries with cost_usd=0.01 and 0.02 → estimated_cost_so_far=0.03
  AC-2: Fresh state with zero terminal entries → estimated_cost_so_far=0.0
  AC-3: METRICS_DB and ORCHESTRATOR_HOME unset → dispatch succeeds, key present, value=0.0

Tests use subprocess invocation (like test_orchestrator_next.py) to test the
full CLI path through bin/orchestrator.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")


def _run_next(fixture_name: str, metrics_db_path: str | None) -> subprocess.CompletedProcess:
    """
    Run `bin/orchestrator next <fixture>` with an optional METRICS_DB.

    If metrics_db_path is None, both METRICS_DB and ORCHESTRATOR_HOME are
    omitted from the environment (no-DB path).
    """
    fixture_path = os.path.join(_FIXTURES_DIR, fixture_name)
    env = os.environ.copy()
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR
    env["PYTHONPATH"] = os.path.join(_WORKTREE_ROOT, "config", "scripts")
    env.pop("ORCHESTRATOR_HOME", None)

    if metrics_db_path is not None:
        env["METRICS_DB"] = metrics_db_path
    else:
        env.pop("METRICS_DB", None)

    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "next", fixture_path],
        capture_output=True,
        text=True,
        env=env,
    )


class TestEstimatedCostSoFar(unittest.TestCase):
    """End-to-end tests for the estimated_cost_so_far action key."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_cost_test_")
        self._metrics_db = os.path.join(self._tmpdir, "test.duckdb")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_ac1_two_rows_sum_to_0_03(self):
        """
        AC-1: state-cost-probe.yaml has two completed entries with cost_usd=0.01
        and cost_usd=0.02. The action dict must contain estimated_cost_so_far=0.03.
        """
        result = _run_next("state-cost-probe.yaml", self._metrics_db)
        self.assertEqual(result.returncode, 1, f"Expected exit 1 (complete_workflow), stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertIn("estimated_cost_so_far", action, "Key 'estimated_cost_so_far' missing from action")
        self.assertAlmostEqual(
            action["estimated_cost_so_far"],
            0.03,
            places=10,
            msg=f"Expected 0.03, got {action['estimated_cost_so_far']}",
        )

    def test_ac2_fresh_state_returns_zero(self):
        """
        AC-2: state-pending-inline.yaml has no terminal entries. The action dict
        must contain estimated_cost_so_far=0.0.
        """
        result = _run_next("state-pending-inline.yaml", self._metrics_db)
        self.assertEqual(result.returncode, 0, f"Expected exit 0 (run_inline), stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertIn("estimated_cost_so_far", action, "Key 'estimated_cost_so_far' missing from action")
        self.assertEqual(
            action["estimated_cost_so_far"],
            0.0,
            f"Expected 0.0 for fresh state, got {action['estimated_cost_so_far']}",
        )

    def test_ac3_no_db_returns_zero(self):
        """
        AC-3: With METRICS_DB and ORCHESTRATOR_HOME unset, dispatch must still
        succeed and the key must be present with value 0.0.
        """
        result = _run_next("state-pending-inline.yaml", metrics_db_path=None)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertIn("estimated_cost_so_far", action, "Key 'estimated_cost_so_far' missing when DB unavailable")
        self.assertEqual(
            action["estimated_cost_so_far"],
            0.0,
            f"Expected 0.0 when no DB available, got {action['estimated_cost_so_far']}",
        )

    def test_key_present_on_all_action_types(self):
        """
        estimated_cost_so_far must appear on every action type: run_inline,
        run_step, retry_step, verify_phase, complete_workflow, blocked.
        """
        fixtures = [
            ("state-pending-inline.yaml", 0),        # run_inline
            ("state-pending-runfield.yaml", 0),       # run_step
            ("state-in-progress-no-ended.yaml", 0),  # retry_step
            ("state-phase-done-needs-verify.yaml", 0),  # verify_phase
            ("state-all-done.yaml", 1),               # complete_workflow
            ("state-escalate.yaml", 2),               # blocked
        ]
        for fixture_name, expected_rc in fixtures:
            with self.subTest(fixture=fixture_name):
                result = _run_next(fixture_name, self._metrics_db)
                self.assertEqual(
                    result.returncode, expected_rc,
                    f"{fixture_name}: unexpected exit code, stderr: {result.stderr}",
                )
                action = json.loads(result.stdout)
                self.assertIn(
                    "estimated_cost_so_far",
                    action,
                    f"{fixture_name}: 'estimated_cost_so_far' missing from action dict",
                )
                self.assertIsInstance(
                    action["estimated_cost_so_far"],
                    float,
                    f"{fixture_name}: 'estimated_cost_so_far' must be a float",
                )


if __name__ == "__main__":
    unittest.main()
