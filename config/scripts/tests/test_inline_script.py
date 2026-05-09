"""HL-287 M3: inline: true + run: executes the script and records the result."""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, shutil, unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_BIN = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")
_CONTRACTS = os.path.join(_FIXTURES_DIR, "step_contracts")


class TestInlineScript(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_inline_script_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_inline_true_with_run_path_executes_and_records(self):
        # Create a minimal script that emits the required output JSON
        scripts_dir = os.path.join(self._tmpdir, "scripts", "inline")
        os.makedirs(scripts_dir)
        script_path = os.path.join(scripts_dir, "step-inline-with-script.sh")
        with open(script_path, "w") as f:
            f.write('#!/usr/bin/env bash\necho \'{"result": "ok"}\'\n')
        os.chmod(script_path, 0o755)

        # Write a state.yaml pointing at the tmpdir as repo_root
        state_yaml = os.path.join(self._tmpdir, "state.yaml")
        with open(state_yaml, "w") as f:
            f.write(f"""\
change_id: test-inline-with-script
schema: feature
version: 1
status: active
phase: implement
repo_root: {self._tmpdir}
worktree_path: {self._tmpdir}
workflow_plan:
  implement:
    active:
      - step-inline-with-script
step_history: []
""")

        # Write a minimal plan.yaml (required by dispatch)
        plan_yaml = os.path.join(self._tmpdir, "plan.yaml")
        with open(plan_yaml, "w") as f:
            f.write("""\
phases:
  - name: implement
    steps:
      - id: step-inline-with-script
        goal: test inline script execution
""")

        env = os.environ.copy()
        env["METRICS_DB"] = os.path.join(self._tmpdir, "test.duckdb")
        env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _CONTRACTS
        result = subprocess.run(
            [sys.executable, _BIN, "next", state_yaml],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        action = json.loads(result.stdout)
        self.assertEqual(action["action"], "recorded")
        self.assertEqual(action["step_id"], "step-inline-with-script")
        self.assertEqual(action["outputs"], {"result": "ok"})


if __name__ == "__main__":
    unittest.main()
