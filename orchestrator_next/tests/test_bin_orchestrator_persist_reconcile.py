"""ORC-81 AC-3: bin/orchestrator persists reconcile mutations to state.yaml."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import duckdb
import yaml

from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "config", "scripts")
_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")
_CONTRACTS = os.path.join(_REPO_ROOT, "config", "scripts", "tests", "fixtures", "step_contracts")


def _run_next(state_yaml: str, metrics_db: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["METRICS_DB"] = metrics_db
    env["PYTHONPATH"] = _SCRIPTS_DIR
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _CONTRACTS
    env.pop("ORCHESTRATOR_HOME", None)
    return subprocess.run(
        [sys.executable, _BIN, "next", state_yaml],
        capture_output=True,
        text=True,
        env=env,
    )


class TestBinOrchestratorPersistReconcile(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orc81_persist_")
        self._metrics_db = os.path.join(self._tmpdir, "metrics.duckdb")
        db = duckdb.connect(self._metrics_db)
        ensure_schema(db)
        upsert_pending_step_event(
            db,
            repo_root="/test/repo",
            change_id="orc81-persist",
            phase="implement",
            step_id="ghost-step",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        db.close()
        self._state_dir = os.path.join(self._tmpdir, "state")
        os.makedirs(self._state_dir)
        self._state_path = os.path.join(self._state_dir, "state.yaml")
        plan_path = os.path.join(self._state_dir, "plan.yaml")
        with open(plan_path, "w") as f:
            f.write(
                textwrap.dedent("""\
                phases:
                - name: implement
                  steps:
                  - id: preview-route
                    agent: developer
                    goal: Test.
                    inputs: []
                    outputs: []
                    rules: []
                """)
            )
        ghost_raw = {
            "step_id": "ghost-step",
            "phase": "implement",
            "status": "in_progress",
            "agent": "developer",
            "attempt": 1,
            "started_at": "2024-01-01T00:00:00Z",
        }
        state = {
            "change_id": "orc81-persist",
            "schema": "feature",
            "version": 1,
            "status": "active",
            "phase": "implement",
            "repo": "test-repo",
            "repo_root": "/test/repo",
            "worktree_path": self._state_dir,
            "workflow_plan": {
                "implement": {"nodes": [{"id": "preview-route"}]},
            },
            "step_history": [ghost_raw],
        }
        with open(self._state_path, "w") as f:
            yaml.safe_dump(state, f, sort_keys=False)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_reconcile_strip_persisted_to_disk(self):
        proc = _run_next(self._state_path, self._metrics_db)
        # dispatch may exit 0 (next step) or 3; reconcile+persist must run first
        with open(self._state_path) as f:
            loaded = yaml.safe_load(f) or {}
        ids = [e.get("step_id") for e in loaded.get("step_history") or []]
        self.assertNotIn("ghost-step", ids, proc.stderr + proc.stdout)

    def test_second_next_does_not_resurrect_ghost(self):
        _run_next(self._state_path, self._metrics_db)
        _run_next(self._state_path, self._metrics_db)
        with open(self._state_path) as f:
            loaded = yaml.safe_load(f) or {}
        ids = [e.get("step_id") for e in loaded.get("step_history") or []]
        self.assertNotIn("ghost-step", ids)


if __name__ == "__main__":
    unittest.main()
