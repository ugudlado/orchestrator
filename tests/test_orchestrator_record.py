"""HL-287 M5: orchestrator record subcommand tests."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_BIN = os.path.join(ORCHESTRATOR_ROOT, "bin", "orchestrator")
_CONTRACTS = os.path.join(_FIXTURES_DIR, "step_contracts")


def _run_record(state_path: str, payload: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _CONTRACTS
    return subprocess.run(
        [sys.executable, _BIN, "done", state_path],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


class TestOrchestratorRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="record-test-")
        self.state_path = os.path.join(self.tmp, "state.yaml")
        with open(self.state_path, "w") as f:
            yaml.safe_dump({
                "change_id": "test-record",
                "schema": "feature",
                "version": 1,
                "status": "active",
                "phase": "implement",
                "worktree_path": "/tmp/w",
                "workflow_plan": {"implement": {"nodes": [
                    {"id": "step-inline-only", "status": "pending"},
                ]}},
                "step_history": [],
            }, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_happy_path_records_and_advances(self):
        """A complete payload with required outputs records and advances next_step."""
        payload = {
            "step_id": "step-inline-only",
            "phase": "implement",
            "status": "completed",
            "agent": "developer",
            "outputs": {"result": "ok"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        r = _run_record(self.state_path, payload)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        response = json.loads(r.stdout)
        self.assertEqual(response["step_id"], "step-inline-only")
        self.assertIn("attempt", response)
        # Next step should be None (phase exhausted)
        self.assertIsNone(response["next_step"])
        # State mutated
        with open(self.state_path) as f:
            state = yaml.safe_load(f)
        self.assertEqual(len(state["step_history"]), 1)
        self.assertEqual(state["step_history"][0]["evidence"]["outputs"], {"result": "ok"})

    def test_missing_required_output_is_validation_error(self):
        """Contract declares outputs: [result]; payload missing it → exit 3."""
        payload = {
            "step_id": "step-inline-only",
            "phase": "implement",
            "status": "completed",
            "agent": "developer",
            "outputs": {},  # missing 'result'
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        r = _run_record(self.state_path, payload)
        self.assertEqual(r.returncode, 3, msg=r.stderr)
        response = json.loads(r.stdout)
        self.assertEqual(response["reason"], "missing_outputs")
        self.assertIn("result", response["missing_outputs"])

    def test_failed_step_with_no_on_failure_leaves_node_non_completed(self):
        """A failed step with no on_failure edge halts AND marks its node failed —
        not completed — so a resume can't treat its dependents as ready."""
        # step-inline-only has no on_failure edge → routing halts.
        payload = {
            "step_id": "step-inline-only",
            "phase": "implement",
            "status": "failed",
            "outputs": {},
            "evidence": {"summary": "script exited 1"},
        }
        r = _run_record(self.state_path, payload)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        with open(self.state_path) as f:
            state = yaml.safe_load(f)
        node = state["workflow_plan"]["implement"]["nodes"][0]
        self.assertEqual(node["status"], "failed")
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["step_history"][0]["status"], "failed")

    def test_phase_transition_when_active_exhausted(self):
        """Multiple steps in phase: second record advances next_step to third."""
        # Re-populate state with 3 active steps
        with open(self.state_path, "w") as f:
            yaml.safe_dump({
                "change_id": "test-record-3",
                "schema": "feature",
                "version": 1,
                "status": "active",
                "phase": "implement",
                "worktree_path": "/tmp/w",
                "workflow_plan": {"implement": {"nodes": [
                    {"id": "step-inline-only", "status": "pending"},
                    {"id": "step-inline-with-script", "status": "pending"},
                ]}},
                "step_history": [],
            }, f)
        payload = {
            "step_id": "step-inline-only",
            "phase": "implement",
            "status": "completed",
            "agent": "developer",
            "outputs": {"result": "ok"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        r = _run_record(self.state_path, payload)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        resp = json.loads(r.stdout)
        # After completing step-inline-only, next pending is step-inline-with-script
        self.assertIsNotNone(resp["next_step"])
        self.assertEqual(resp["next_step"]["step_id"], "step-inline-with-script")


if __name__ == "__main__":
    unittest.main()
