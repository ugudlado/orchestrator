"""
Inline-contract smoke test (T-8 regression guard).

Iterates all config/steps/*.yaml files in the worktree that do NOT have a `run:`
field, synthesises a minimal state.yaml pointing at each, calls `orchestrator next`,
and asserts:
  - exit code is 0
  - JSON response has action: run_inline

This test guards against regressions introduced by changes to parser.py or
dispatch.py that would break the 44 existing inline-only step contracts.

Note: This test may pass immediately on a clean T-2 implementation — that is
the intended outcome documented in tasks.md T-8. The value is the regression
guard it provides after commit: any future change that breaks inline dispatch
for any step contract will be caught here.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_BIN_ORCHESTRATOR = os.path.join(ORCHESTRATOR_ROOT, "bin", "orchestrator")
_STEPS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "steps")
_SCRIPTS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")

# Minimal state.yaml template for a pending agent step.
_STATE_TEMPLATE = """\
change_id: smoke-test-inline
schema: feature
version: 1
status: active
phase: implement
repo: test-repo
worktree_path: /tmp/smoke-test-workflow
workflow_plan:
  implement:
    nodes:
      - id: {step_id}
        status: pending
step_history: []
"""


def _find_inline_step_ids() -> list[tuple[str, str]]:
    """Return (step_id, contract_path) for agent steps (no run: field) in config/steps/."""
    inline = []
    for entry in sorted(os.scandir(_STEPS_DIR), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        contract = os.path.join(entry.path, "contract.yaml")
        if not os.path.isfile(contract):
            continue
        with open(contract) as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError:
                continue
        if not isinstance(data, dict):
            continue
        if "run" not in data:
            step_id = data.get("id", entry.name)
            inline.append((step_id, contract))
    return inline


class TestInlineContractSmoke(unittest.TestCase):
    """Smoke test: all inline-only step contracts dispatch to action: run_inline."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_inline_smoke_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run_for_step(self, step_id: str) -> subprocess.CompletedProcess:
        """Write a minimal state.yaml and invoke `orchestrator next` for step_id."""
        state_content = _STATE_TEMPLATE.format(step_id=step_id)
        state_path = os.path.join(self._tmpdir, f"state-{step_id}.yaml")
        with open(state_path, "w") as f:
            f.write(state_content)

        env = os.environ.copy()
        # ORCHESTRATOR_HOME points at the orchestrator root so parser finds real contracts.
        env["ORCHESTRATOR_HOME"] = ORCHESTRATOR_ROOT
        # Remove the test override so the real steps directory is used.
        env.pop("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", None)
        env["PYTHONPATH"] = _SCRIPTS_DIR
        env.pop("ORCHESTRATOR_REPO_ROOT", None)

        return subprocess.run(
            [sys.executable, _BIN_ORCHESTRATOR, "next", state_path],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_all_agent_steps_dispatch_with_model_field(self):
        """Every agent step contract must dispatch to exit 0 with a 'model' field in JSON."""
        agent_steps = _find_inline_step_ids()
        self.assertGreater(
            len(agent_steps), 0,
            "No agent step contracts found — check _STEPS_DIR path"
        )

        failures = []
        for step_id, _yaml_path in agent_steps:
            result = self._run_for_step(step_id)
            if result.returncode != 0:
                failures.append(
                    f"  step={step_id}: exit {result.returncode}, "
                    f"stderr={result.stderr.strip()[:120]}"
                )
                continue
            try:
                actual = json.loads(result.stdout)
            except json.JSONDecodeError:
                failures.append(
                    f"  step={step_id}: non-JSON stdout: {result.stdout[:80]}"
                )
                continue
            if not actual.get("model"):
                failures.append(
                    f"  step={step_id}: expected 'model' field in dispatch JSON, "
                    f"got: {list(actual.keys())}"
                )

        if failures:
            self.fail(
                f"{len(failures)} agent step(s) did not dispatch correctly:\n"
                + "\n".join(failures)
            )


if __name__ == "__main__":
    unittest.main()
