"""
Fixture-driven dispatcher tests for `orchestrator next`.

Each test invokes `bin/orchestrator next <fixture>` as a subprocess and
asserts the exit code (0=action, 1=complete_workflow, 2=blocked, 3=error).
"""
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
