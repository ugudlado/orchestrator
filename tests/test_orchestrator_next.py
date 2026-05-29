"""
Fixture-driven dispatcher tests for `orchestrator next`.

Each test:
1. Invokes `bin/orchestrator next <fixture>` as a subprocess.
2. Compares stdout (byte-for-byte) to the corresponding golden JSON file.
3. Asserts the state.yaml mtime is unchanged (CLI must be pure-read).
4. Asserts the correct exit code.

Exit codes: 0=action, 1=complete_workflow, 2=blocked, 3=error.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Paths are relative to the worktree root, resolved from this file's location.
_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_GOLDEN_DIR = os.path.join(_HERE, "golden")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")


def _run_next(fixture_name: str, metrics_db_path: str) -> subprocess.CompletedProcess:
    """Run `bin/orchestrator next <fixture>` and capture result.

    metrics_db_path must be a per-test temp path so dispatcher tests do not
    create a side-effect metrics.duckdb in the worktree root.
    ORCHESTRATOR_HOME is intentionally omitted — tests use METRICS_DB directly.
    """
    fixture_path = os.path.join(_FIXTURES_DIR, fixture_name)
    env = os.environ.copy()
    # Point upsert at an isolated per-test DB (not the worktree root).
    env["METRICS_DB"] = metrics_db_path
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR
    # Ensure the worktree scripts are on the path
    env["PYTHONPATH"] = os.path.join(_WORKTREE_ROOT, "config", "scripts")
    # Remove ORCHESTRATOR_HOME so the fallback path is never used.
    env.pop("ORCHESTRATOR_HOME", None)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "next", fixture_path],
        capture_output=True,
        text=True,
        env=env,
    )


def _load_golden(golden_name: str) -> str:
    """Load the golden JSON file and return its content."""
    golden_path = os.path.join(_GOLDEN_DIR, golden_name)
    with open(golden_path, "r") as f:
        return f.read()


class TestOrchestratorNextDispatcher(unittest.TestCase):
    """6 fixture-driven dispatcher tests for orchestrator next."""

    def setUp(self):
        # Isolated tempdir per test — metrics.duckdb goes here, not the worktree root.
        self._tmpdir = tempfile.mkdtemp(prefix="orch_dispatcher_test_")
        self._metrics_db = os.path.join(self._tmpdir, "test.duckdb")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, fixture_name: str) -> subprocess.CompletedProcess:
        """Convenience wrapper that passes the per-test metrics DB path."""
        return _run_next(fixture_name, self._metrics_db)

    def _assert_json_matches_golden(self, stdout: str, golden_name: str) -> None:
        """Parse stdout as JSON and compare to golden (key-sorted, indented)."""
        actual = json.loads(stdout)
        golden_text = _load_golden(golden_name)
        expected = json.loads(golden_text)
        self.assertEqual(
            actual,
            expected,
            f"JSON output does not match golden {golden_name}.\n"
            f"Actual:   {json.dumps(actual, sort_keys=True, indent=2)}\n"
            f"Expected: {json.dumps(expected, sort_keys=True, indent=2)}",
        )
        # Also assert that the raw stdout matches the golden byte-for-byte
        # (ensures deterministic sorted-keys pretty output)
        canonical_actual = json.dumps(actual, sort_keys=True, indent=2) + "\n"
        self.assertEqual(
            stdout,
            canonical_actual,
            f"Stdout is not canonical sorted-keys JSON.\n"
            f"Got:      {repr(stdout)}\n"
            f"Expected: {repr(canonical_actual)}",
        )

    def _get_mtime(self, fixture_name: str) -> float:
        fixture_path = os.path.join(_FIXTURES_DIR, fixture_name)
        return os.path.getmtime(fixture_path)

    def test_pending_inline_returns_run_inline(self):
        """state-pending-inline.yaml: next step has no run: → action: run_inline, exit 0."""
        fixture = "state-pending-inline.yaml"
        mtime_before = self._get_mtime(fixture)
        result = self._run(fixture)
        mtime_after = self._get_mtime(fixture)

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-pending-inline.json")
        self.assertEqual(mtime_before, mtime_after, "state.yaml mtime changed — CLI must be pure-read")

    def test_pending_runfield_returns_run_step(self):
        """state-pending-runfield.yaml: next step has run: → action: run_step, exit 0."""
        fixture = "state-pending-runfield.yaml"
        mtime_before = self._get_mtime(fixture)
        result = self._run(fixture)
        mtime_after = self._get_mtime(fixture)

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-pending-runfield.json")
        self.assertEqual(mtime_before, mtime_after, "state.yaml mtime changed — CLI must be pure-read")

    def test_in_progress_no_ended_returns_resume_step(self):
        """state-in-progress-no-ended.yaml: last entry in_progress without ended_at → resume_step."""
        fixture = "state-in-progress-no-ended.yaml"
        mtime_before = self._get_mtime(fixture)
        result = self._run(fixture)
        mtime_after = self._get_mtime(fixture)

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-in-progress-no-ended.json")
        self.assertEqual(mtime_before, mtime_after, "state.yaml mtime changed — CLI must be pure-read")

    def test_phase_done_needs_verify_returns_verify_phase(self):
        """state-phase-done-needs-verify.yaml: all steps done, phase has verify block → verify_phase."""
        fixture = "state-phase-done-needs-verify.yaml"
        mtime_before = self._get_mtime(fixture)
        result = self._run(fixture)
        mtime_after = self._get_mtime(fixture)

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-phase-done-needs-verify.json")
        self.assertEqual(mtime_before, mtime_after, "state.yaml mtime changed — CLI must be pure-read")

    def test_all_done_returns_complete_workflow(self):
        """state-all-done.yaml: every phase complete → complete_workflow, exit 1."""
        fixture = "state-all-done.yaml"
        mtime_before = self._get_mtime(fixture)
        result = self._run(fixture)
        mtime_after = self._get_mtime(fixture)

        self.assertEqual(result.returncode, 1, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-all-done.json")
        self.assertEqual(mtime_before, mtime_after, "state.yaml mtime changed — CLI must be pure-read")

    def test_escalate_returns_blocked(self):
        """state-escalate.yaml: last entry escalate_to_architect → blocked, exit 2."""
        fixture = "state-escalate.yaml"
        mtime_before = self._get_mtime(fixture)
        result = self._run(fixture)
        mtime_after = self._get_mtime(fixture)

        self.assertEqual(result.returncode, 2, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-escalate.json")
        self.assertEqual(mtime_before, mtime_after, "state.yaml mtime changed — CLI must be pure-read")


if __name__ == "__main__":
    unittest.main()
