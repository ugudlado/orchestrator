"""HL-287 M3: inline: true + run: produces run_inline action with script path."""
from __future__ import annotations
import json, os, subprocess, sys, unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_BIN = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")
_CONTRACTS = os.path.join(_FIXTURES_DIR, "step_contracts")


class TestInlineScript(unittest.TestCase):
    def test_inline_true_with_run_path_produces_run_inline_action(self):
        env = os.environ.copy()
        env["METRICS_DB"] = "/tmp/test-inline-script.duckdb"
        env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _CONTRACTS
        result = subprocess.run(
            [sys.executable, _BIN, "next",
             os.path.join(_FIXTURES_DIR, "state-inline-with-script.yaml")],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        action = json.loads(result.stdout)
        self.assertEqual(action["action"], "run_inline")
        self.assertEqual(action["step_id"], "step-inline-with-script")
        self.assertEqual(action["agent"], "inline")
        self.assertEqual(action["run"], "scripts/inline/step-inline-with-script.sh")
        self.assertEqual(action["expected_outputs"], ["result"])


if __name__ == "__main__":
    unittest.main()
