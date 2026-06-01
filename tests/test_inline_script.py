"""HL-287 M3: inline: true + run: executes the script and records the result."""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, shutil, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_BIN = os.path.join(ORCHESTRATOR_ROOT, "bin", "orchestrator")
_CONTRACTS = os.path.join(_FIXTURES_DIR, "step_contracts")


class TestInlineScript(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_inline_script_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_inline_script_executes_and_records_to_state_yaml(self):
        """Run step with run: contract — script executes, step recorded in state.yaml, exit 0."""
        import yaml as _yaml

        state_yaml = os.path.join(self._tmpdir, "state.yaml")
        with open(state_yaml, "w") as f:
            _yaml.safe_dump({
                "change_id": "test-inline-with-script",
                "schema": "feature",
                "version": 1,
                "status": "active",
                "phase": "implement",
                "repo_root": self._tmpdir,
                "worktree_path": self._tmpdir,
                "workflow_plan": {"implement": {"nodes": [
                    {"id": "step-inline-with-script", "status": "pending"},
                ]}},
                "step_history": [],
            }, f)

        env = os.environ.copy()
        env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _CONTRACTS
        result = subprocess.run(
            [sys.executable, _BIN, "next", state_yaml],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        # Inline steps exit 0 with no JSON — the driver loops to call next again
        self.assertEqual(result.stdout.strip(), "", "inline script path must emit no JSON")
        # State.yaml was written with the completed step
        with open(state_yaml) as f:
            state = _yaml.safe_load(f)
        history = state.get("step_history") or []
        self.assertEqual(len(history), 1, "expected one step_history entry")
        self.assertEqual(history[0]["step_id"], "step-inline-with-script")
        self.assertEqual(history[0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
