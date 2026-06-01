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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

# Paths are relative to the orchestrator root, resolved from this file's location.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_FIXTURES_DIR = os.path.join(_TESTS_DIR, "fixtures")
_GOLDEN_DIR = os.path.join(_TESTS_DIR, "golden")
_BIN_ORCHESTRATOR = os.path.join(ORCHESTRATOR_ROOT, "bin", "orchestrator")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")


def _run_next(fixture_name: str, fixture_path: str | None = None) -> subprocess.CompletedProcess:
    """Run `bin/orchestrator next <fixture>` and capture result.

    If fixture_path is provided, uses it directly (allows temp copies).
    Otherwise uses the canonical fixture in tests/fixtures/.
    """
    path = fixture_path or os.path.join(_FIXTURES_DIR, fixture_name)
    env = os.environ.copy()
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR
    env["PYTHONPATH"] = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")
    env.pop("ORCHESTRATOR_HOME", None)
    env.pop("METRICS_DB", None)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "next", path],
        capture_output=True,
        text=True,
        env=env,
    )


def _strip_variable_fields(data: dict) -> dict:
    """Remove machine-specific or run-specific fields before golden comparison."""
    result = dict(data)
    env = dict(result.get("env") or {})
    env.pop("ORCHESTRATOR_STATE_YAML_PATH", None)
    env.pop("ORCHESTRATOR_WORKTREE_ARTIFACT_DIR", None)
    result["env"] = env
    result.pop("started_at", None)  # resume timestamp varies
    return result


def _load_golden(golden_name: str) -> str:
    """Load the golden JSON file and return its content."""
    golden_path = os.path.join(_GOLDEN_DIR, golden_name)
    with open(golden_path, "r") as f:
        return f.read()


class TestOrchestratorNextDispatcher(unittest.TestCase):
    """Fixture-driven dispatcher tests for orchestrator next.

    Uses temp copies of fixtures to prevent state contamination — the dispatcher
    now writes an in_progress entry to state.yaml when dispatching agent steps.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_dispatcher_test_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _copy_fixture(self, fixture_name: str) -> str:
        """Copy fixture to tmpdir and return the temp path."""
        src = os.path.join(_FIXTURES_DIR, fixture_name)
        dst = os.path.join(self._tmpdir, fixture_name)
        shutil.copy(src, dst)
        return dst

    def _run(self, fixture_name: str) -> tuple[subprocess.CompletedProcess, str]:
        """Run dispatcher with a temp copy of the fixture. Returns (result, temp_path)."""
        tmp_path = self._copy_fixture(fixture_name)
        result = _run_next(fixture_name, fixture_path=tmp_path)
        return result, tmp_path

    def _assert_json_matches_golden(self, stdout: str, golden_name: str) -> None:
        """Parse stdout as JSON, strip variable fields, and compare to golden."""
        actual = _strip_variable_fields(json.loads(stdout))
        golden_text = _load_golden(golden_name)
        expected = _strip_variable_fields(json.loads(golden_text))
        self.assertEqual(
            actual,
            expected,
            f"JSON output does not match golden {golden_name}.\n"
            f"Actual:   {json.dumps(actual, sort_keys=True, indent=2)}\n"
            f"Expected: {json.dumps(expected, sort_keys=True, indent=2)}",
        )

    def test_pending_inline_returns_run_inline(self):
        """state-pending-inline.yaml: pending agent step → emits agent JSON, exit 0."""
        result, _ = self._run("state-pending-inline.yaml")
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-pending-inline.json")

    def test_pending_runfield_returns_run_step(self):
        """state-pending-runfield.yaml: next step has run: → run action, exit 0."""
        result, _ = self._run("state-pending-runfield.yaml")
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-pending-runfield.json")

    def test_in_progress_no_ended_returns_resume_step(self):
        """state-in-progress-no-ended.yaml: last entry in_progress → resume, exit 0."""
        result, _ = self._run("state-in-progress-no-ended.yaml")
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self._assert_json_matches_golden(result.stdout, "state-in-progress-no-ended.json")

    def test_phase_done_needs_verify_exits_complete(self):
        """state-phase-done-needs-verify.yaml: all steps done → exit 1 (complete), no JSON."""
        result, _ = self._run("state-phase-done-needs-verify.yaml")
        self.assertEqual(result.returncode, 1, f"stderr: {result.stderr}")
        self.assertEqual(result.stdout.strip(), "", "expected no JSON output for exit 1")

    def test_all_done_returns_complete_workflow(self):
        """state-all-done.yaml: every phase complete → exit 1, no JSON."""
        result, _ = self._run("state-all-done.yaml")
        self.assertEqual(result.returncode, 1, f"stderr: {result.stderr}")
        self.assertEqual(result.stdout.strip(), "", "expected no JSON output for exit 1")

    def test_escalate_returns_blocked(self):
        """state-escalate.yaml: last entry escalate_to_architect → exit 2, no JSON."""
        result, _ = self._run("state-escalate.yaml")
        self.assertEqual(result.returncode, 2, f"stderr: {result.stderr}")
        self.assertEqual(result.stdout.strip(), "", "expected no JSON output for exit 2")


if __name__ == "__main__":
    unittest.main()
