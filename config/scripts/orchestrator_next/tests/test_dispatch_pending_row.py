"""
Subprocess-driven tests for bin/orchestrator post-dispatch pending write + reconcile
integration — T-8 (RED) → T-9 (GREEN).

Scenarios:
  (a) After `next` returns run_step or run_inline:
      - DB has an in_progress row at (repo_root, change_id, phase, step_id, attempt).
      - state.yaml step_history has a matching in_progress entry.

  (b) For verify_phase / complete_workflow / blocked actions:
      - DB has NO in_progress row for the current (phase, step_id).
      - state.yaml has NO in_progress entry.

  (c) attempt=2 in_progress coexists with attempt=1 terminal row:
      - Seed state with an attempt=1, status=failed entry.
      - Call next → action=run_step with attempt=2 (first pending call on step
        that has a prior failed attempt).
      - Assert DB has BOTH (attempt=1, status=failed) and (attempt=2, status=in_progress).

Tests use tmp_path (fresh directory per test) + a minimal plan.yaml to satisfy dispatch.
METRICS_DB is pointed at an isolated per-test .duckdb file.
ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE is wired to the standard fixtures dir so step
contracts resolve correctly.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_SCRIPTS_DIR = os.path.join(_WORKTREE_ROOT, "config", "scripts")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")
# Reuse step contracts from the existing tests/fixtures directory.
_STEP_CONTRACTS_DIR = os.path.join(
    _WORKTREE_ROOT, "config", "scripts", "tests", "fixtures", "step_contracts"
)

# ---------------------------------------------------------------------------
# State / plan YAML helpers
# ---------------------------------------------------------------------------

_PLAN_YAML_TEMPLATE = textwrap.dedent("""\
    phases:
    - name: implement
      steps:
      - id: {step_id}
        agent: {agent}
        goal: Test step.
        inputs: []
        outputs: []
        rules: []
""")


def _write_plan_yaml(directory: str, step_id: str, agent: str = "developer") -> str:
    """Write a minimal plan.yaml alongside state.yaml and return its path."""
    plan_path = os.path.join(directory, "plan.yaml")
    with open(plan_path, "w") as f:
        f.write(_PLAN_YAML_TEMPLATE.format(step_id=step_id, agent=agent))
    return plan_path


def _write_state_yaml(directory: str, content: str) -> str:
    """Write state.yaml into directory and return its path."""
    state_path = os.path.join(directory, "state.yaml")
    with open(state_path, "w") as f:
        f.write(textwrap.dedent(content))
    return state_path


def _run_next(state_yaml_path: str, metrics_db_path: str) -> subprocess.CompletedProcess:
    """Invoke `bin/orchestrator next <state_yaml_path>` with an isolated METRICS_DB."""
    env = os.environ.copy()
    env["METRICS_DB"] = metrics_db_path
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR
    env["PYTHONPATH"] = _SCRIPTS_DIR
    env.pop("ORCHESTRATOR_HOME", None)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "next", state_yaml_path],
        capture_output=True,
        text=True,
        env=env,
    )


def _count_in_progress(db: duckdb.DuckDBPyConnection, change_id: str) -> int:
    """Count in_progress rows for a change_id."""
    return db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id = ? AND status = 'in_progress'",
        [change_id],
    ).fetchone()[0]


def _get_step_events(
    db: duckdb.DuckDBPyConnection, change_id: str
) -> list[dict]:
    """Return all step_events rows for change_id as list of dicts."""
    rows = db.execute(
        "SELECT phase, step_id, attempt, status, agent_name, started_at "
        "FROM step_events WHERE change_id = ? ORDER BY attempt, status",
        [change_id],
    ).fetchall()
    return [
        {
            "phase": r[0],
            "step_id": r[1],
            "attempt": r[2],
            "status": r[3],
            "agent_name": r[4],
            "started_at": r[5],
        }
        for r in rows
    ]


def _load_state_yaml(state_yaml_path: str) -> dict:
    """Load and return parsed state.yaml."""
    import yaml  # noqa: PLC0415
    with open(state_yaml_path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestDispatchPendingRow(unittest.TestCase):
    """
    Subprocess tests asserting that bin/orchestrator writes (or withholds) an
    in_progress row after dispatch() returns, per the action verb.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_pending_row_test_")
        self._metrics_db_path = os.path.join(self._tmpdir, "test.duckdb")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Scenario (a) — run_step writes an in_progress row + state.yaml entry
    # -----------------------------------------------------------------------

    def test_run_step_writes_in_progress_db_row(self):
        """
        (a-run_step) After `next` returns run_step, DB has an in_progress row at
        (repo_root, change_id, phase, step_id, attempt=1) with a non-NULL started_at.
        """
        state_dir = tempfile.mkdtemp(prefix="state_run_step_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-pending-run-step
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-with-run
            step_history: []
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-with-run", agent="discoverer")

        result = _run_next(state_path, self._metrics_db_path)

        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertEqual(action.get("action"), "run_step",
                         f"Expected run_step action, got: {action.get('action')}")

        db = duckdb.connect(self._metrics_db_path)
        try:
            from orchestrator_next.upsert import ensure_schema  # noqa: PLC0415
            ensure_schema(db)

            rows = _get_step_events(db, "test-pending-run-step")
            self.assertEqual(len(rows), 1, f"Expected exactly 1 row, got {len(rows)}: {rows}")
            row = rows[0]
            self.assertEqual(row["status"], "in_progress")
            self.assertEqual(row["phase"], "implement")
            self.assertEqual(row["step_id"], "step-with-run")
            self.assertEqual(row["attempt"], 1)
            self.assertIsNotNone(row["started_at"])
        finally:
            db.close()

    def test_run_step_writes_in_progress_state_yaml_entry(self):
        """
        (a-run_step state.yaml) After `next` returns run_step, state.yaml has a
        matching in_progress entry in step_history.
        """
        state_dir = tempfile.mkdtemp(prefix="state_rs_yaml_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-rs-yaml
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-with-run
            step_history: []
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-with-run", agent="discoverer")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")

        state = _load_state_yaml(state_path)
        history = state.get("step_history") or []
        in_progress = [
            e for e in history
            if isinstance(e, dict)
            and e.get("status") == "in_progress"
            and e.get("step_id") == "step-with-run"
            and e.get("phase") == "implement"
        ]
        self.assertEqual(
            len(in_progress), 1,
            f"Expected exactly 1 in_progress entry in state.yaml, got: {history}",
        )
        entry = in_progress[0]
        self.assertEqual(entry.get("attempt"), 1)
        self.assertIsNotNone(entry.get("started_at"))

    def test_run_inline_writes_in_progress_db_row(self):
        """
        (a-run_inline) After `next` returns run_inline, DB has an in_progress row.
        """
        state_dir = tempfile.mkdtemp(prefix="state_run_inline_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-pending-run-inline
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-inline-only
            step_history: []
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-inline-only", agent="inline")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertEqual(action.get("action"), "run_inline",
                         f"Expected run_inline action, got: {action.get('action')}")

        db = duckdb.connect(self._metrics_db_path)
        try:
            rows = _get_step_events(db, "test-pending-run-inline")
            self.assertEqual(len(rows), 1, f"Expected exactly 1 in_progress row, got {len(rows)}")
            row = rows[0]
            self.assertEqual(row["status"], "in_progress")
            self.assertEqual(row["step_id"], "step-inline-only")
            self.assertIsNotNone(row["started_at"])
        finally:
            db.close()

    def test_run_inline_writes_in_progress_state_yaml_entry(self):
        """
        (a-run_inline state.yaml) After `next` returns run_inline, state.yaml has a
        matching in_progress entry.
        """
        state_dir = tempfile.mkdtemp(prefix="state_ri_yaml_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-ri-yaml
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-inline-only
            step_history: []
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-inline-only", agent="inline")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")

        state = _load_state_yaml(state_path)
        history = state.get("step_history") or []
        in_progress = [
            e for e in history
            if isinstance(e, dict) and e.get("status") == "in_progress"
        ]
        self.assertEqual(len(in_progress), 1,
                         f"Expected 1 in_progress entry in state.yaml, got: {history}")

    # -----------------------------------------------------------------------
    # Scenario (b) — non-step verbs do NOT write a pending row
    # -----------------------------------------------------------------------

    def test_verify_phase_no_in_progress_row(self):
        """
        (b-verify_phase) verify_phase action must not write an in_progress DB row.
        """
        state_dir = tempfile.mkdtemp(prefix="state_verify_phase_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-verify-phase
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-inline-only
                verify:
                  commands:
                    - echo check
                  assertions:
                    - All steps completed
            step_history:
              - step_id: step-inline-only
                phase: implement
                status: completed
                agent: inline
                attempt: 1
                started_at: "2026-04-18T10:00:00Z"
                ended_at: "2026-04-18T10:30:00Z"
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-inline-only", agent="inline")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertEqual(action.get("action"), "verify_phase",
                         f"Expected verify_phase, got: {action.get('action')}")

        db = duckdb.connect(self._metrics_db_path)
        try:
            count = _count_in_progress(db, "test-verify-phase")
            self.assertEqual(count, 0,
                             f"verify_phase must not write an in_progress row; got {count} rows")
        finally:
            db.close()

        # Also assert state.yaml has no in_progress entry
        state = _load_state_yaml(state_path)
        in_progress = [
            e for e in (state.get("step_history") or [])
            if isinstance(e, dict) and e.get("status") == "in_progress"
        ]
        self.assertEqual(len(in_progress), 0,
                         f"verify_phase must not add in_progress to state.yaml, got: {in_progress}")

    def test_complete_workflow_no_in_progress_row(self):
        """
        (b-complete_workflow) complete_workflow action must not write an in_progress DB row.
        """
        state_dir = tempfile.mkdtemp(prefix="state_complete_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-complete-workflow
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-inline-only
            step_history:
              - step_id: step-inline-only
                phase: implement
                status: completed
                agent: inline
                attempt: 1
                started_at: "2026-04-18T10:00:00Z"
                ended_at: "2026-04-18T10:30:00Z"
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-inline-only", agent="inline")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 1, f"Expected exit 1 (complete_workflow), stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertEqual(action.get("action"), "complete_workflow",
                         f"Expected complete_workflow, got: {action.get('action')}")

        db = duckdb.connect(self._metrics_db_path)
        try:
            count = _count_in_progress(db, "test-complete-workflow")
            self.assertEqual(count, 0,
                             f"complete_workflow must not write an in_progress row; got {count} rows")
        finally:
            db.close()

    def test_blocked_no_in_progress_row(self):
        """
        (b-blocked) blocked action (escalate_to_architect in step_history) must not
        write an in_progress DB row.
        """
        state_dir = tempfile.mkdtemp(prefix="state_blocked_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-blocked-no-pending
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-inline-only
            step_history:
              - step_id: step-inline-only
                phase: implement
                status: escalate_to_architect
                agent: developer
                attempt: 1
                started_at: "2026-04-18T10:00:00Z"
                ended_at: "2026-04-18T10:30:00Z"
                escalation:
                  type: missing_coverage
                  task_id: T-1
                  context: Test escalation.
                  question: What should be done?
                  attempted: Tried both approaches.
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-inline-only", agent="developer")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 2, f"Expected exit 2 (blocked), stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertEqual(action.get("action"), "blocked",
                         f"Expected blocked, got: {action.get('action')}")

        db = duckdb.connect(self._metrics_db_path)
        try:
            count = _count_in_progress(db, "test-blocked-no-pending")
            self.assertEqual(count, 0,
                             f"blocked must not write an in_progress row; got {count} rows")
        finally:
            db.close()

    # -----------------------------------------------------------------------
    # Scenario (c) — attempt=2 in_progress coexists with attempt=1 terminal
    # -----------------------------------------------------------------------

    def test_attempt2_in_progress_coexists_with_attempt1_failed(self):
        """
        (c) Seed state with attempt=1 failed entry (written to DB via pre-dispatch
        terminal upsert). Call next → action=run_step with attempt=2. Assert DB has
        BOTH (attempt=1, status=failed) AND (attempt=2, status=in_progress) — two
        separate PK entries, coexisting.
        """
        state_dir = tempfile.mkdtemp(prefix="state_attempt2_", dir=self._tmpdir)
        state_path = _write_state_yaml(
            state_dir,
            """\
            change_id: test-attempt2-coexist
            schema: feature
            version: 1
            status: active
            phase: implement
            repo: test-repo
            repo_root: /test/repo
            worktree_path: /tmp/test-workflow
            workflow_plan:
              implement:
                active:
                  - step-with-run
            step_history:
              - step_id: step-with-run
                phase: implement
                status: failed
                agent: discoverer
                attempt: 1
                started_at: "2026-04-18T10:00:00Z"
                ended_at: "2026-04-18T10:30:00Z"
                usage: {}
            """,
        )
        _write_plan_yaml(state_dir, step_id="step-with-run", agent="discoverer")

        result = _run_next(state_path, self._metrics_db_path)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, stderr: {result.stderr}")
        action = json.loads(result.stdout)
        self.assertEqual(action.get("action"), "run_step",
                         f"Expected run_step, got: {action.get('action')}")
        self.assertEqual(action.get("attempt"), 2,
                         f"Expected attempt=2 (after failed attempt=1), got: {action.get('attempt')}")

        db = duckdb.connect(self._metrics_db_path)
        try:
            rows = _get_step_events(db, "test-attempt2-coexist")

            statuses = {(r["attempt"], r["status"]) for r in rows}
            self.assertIn(
                (1, "failed"), statuses,
                f"Expected (attempt=1, status=failed) row, got: {rows}",
            )
            self.assertIn(
                (2, "in_progress"), statuses,
                f"Expected (attempt=2, status=in_progress) row, got: {rows}",
            )
            self.assertEqual(
                len(rows), 2,
                f"Expected exactly 2 rows (1 failed + 1 in_progress), got: {rows}",
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
