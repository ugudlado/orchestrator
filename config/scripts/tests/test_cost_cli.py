"""
Subprocess tests for `orchestrator cost` CLI subcommand (T-3).

Each test:
  - Seeds a fresh tempfile DuckDB with known step_events + tool_calls rows.
  - Invokes `bin/orchestrator cost ...` as a subprocess.
  - Asserts exit code, stdout content, and/or stderr content.

Scenarios covered:
  1. feature default md (--change-id, default --format md) → exit 0, 8 sections
  2. --by step → exit 0, single table, no extra sections
  3. --by agent → exit 0, single agent table
  4. --by tool → exit 0, single tool table
  5. --repo default → exit 0, feature-level list
  6. --repo --by agent → exit 0, agent aggregation
  7. --repo --by tool → exit 0, tool aggregation
  8. --format json parseability → exit 0, 8 documented top-level keys
  9. slug-guard rejection (AC-10) → exit 3, stderr contains slug-guard message
  10. byte-identical repeated runs (AC-9) → two runs produce identical stdout
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SCRIPTS_DIR = os.path.join(_WORKTREE_ROOT, "config", "scripts")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")

# Section headings that must appear in a full feature report
_FEATURE_SECTIONS = [
    "## Executive Summary",
    "## Per-Phase",
    "## Per-Agent",
    "## Per-Model",
    "## Native Tools",
    "## MCP Calls",
    "## Per-Agent Tool Use",
    "## Anomalies",
]

_JSON_REQUIRED_KEYS = {
    "totals", "per_phase", "per_agent", "per_model",
    "native_tools", "mcp_calls", "per_agent_tools", "anomalies",
}


def _seed_db(db_path: str, repo_root: str, change_id: str = "test-feature") -> None:
    """Seed a minimal DuckDB with step_events + tool_calls rows."""
    import duckdb
    from orchestrator_next.upsert import ensure_schema, upsert_step_event
    from orchestrator_next.parser import StepHistoryEntry

    db = duckdb.connect(db_path)
    ensure_schema(db)

    entries = [
        StepHistoryEntry(
            step_id="step-specify",
            phase="specify",
            status="completed",
            agent="specifier",
            attempt=1,
            started_at="2026-04-18T10:00:00Z",
            ended_at="2026-04-18T10:30:00Z",
            usage={
                "input_tokens": 5000,
                "output_tokens": 800,
                "cost_usd": 0.10,
                "duration_ms": 30000,
                "model": "claude-sonnet-4-5",
                "tool_calls": {"Read": 5, "Grep": 3},
            },
            escalation=None,
            raw={},
        ),
        StepHistoryEntry(
            step_id="step-implement",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-04-18T10:31:00Z",
            ended_at="2026-04-18T11:00:00Z",
            usage={
                "input_tokens": 12000,
                "output_tokens": 1800,
                "cost_usd": 0.47,
                "duration_ms": 90000,
                "model": "claude-sonnet-4-5",
                "tool_calls": {"Bash": 4, "Edit": 6, "mcp__pal__thinkdeep": 2},
            },
            escalation=None,
            raw={},
        ),
    ]
    ctx = {"repo_root": repo_root, "change_id": change_id}
    for e in entries:
        upsert_step_event(db, e, ctx)
    db.close()


def _run_cost(
    args: list[str],
    db_path: str,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["METRICS_DB"] = db_path
    env["PYTHONPATH"] = _SCRIPTS_DIR
    env.pop("ORCHESTRATOR_HOME", None)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "cost"] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or _WORKTREE_ROOT,
    )


class TestCostCLI(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_cost_test_")
        self._db_path = os.path.join(self._tmpdir, "test.duckdb")
        self._repo_root = "/test/myrepo"
        self._change_id = "test-feature"

        # Add scripts dir to sys.path so _seed_db can import
        if _SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, _SCRIPTS_DIR)

        _seed_db(self._db_path, self._repo_root, self._change_id)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        return _run_cost(args, self._db_path, cwd=cwd)

    # ------------------------------------------------------------------
    # Scenario 1: feature default md
    # ------------------------------------------------------------------
    def test_feature_default_md_exit_zero(self):
        """--change-id X → exit 0."""
        result = self._run(["--change-id", self._change_id])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_feature_default_md_eight_sections(self):
        """--change-id X → all 8 section headings present."""
        result = self._run(["--change-id", self._change_id])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        for section in _FEATURE_SECTIONS:
            self.assertIn(section, result.stdout, f"Missing section: {section}")

    # ------------------------------------------------------------------
    # Scenario 2: --by step
    # ------------------------------------------------------------------
    def test_by_step_single_table(self):
        """--by step → '## By Step' heading; none of the other 8 sections."""
        result = self._run(["--change-id", self._change_id, "--by", "step"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("## By Step", result.stdout)
        for section in _FEATURE_SECTIONS:
            self.assertNotIn(section, result.stdout, f"Unexpected section: {section}")

    # ------------------------------------------------------------------
    # Scenario 3: --by agent
    # ------------------------------------------------------------------
    def test_by_agent_single_table(self):
        """--by agent → '## By Agent' heading; none of the 8 sections."""
        result = self._run(["--change-id", self._change_id, "--by", "agent"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("## By Agent", result.stdout)
        for section in _FEATURE_SECTIONS:
            self.assertNotIn(section, result.stdout, f"Unexpected section: {section}")

    # ------------------------------------------------------------------
    # Scenario 4: --by tool
    # ------------------------------------------------------------------
    def test_by_tool_single_table(self):
        """--by tool → '## By Tool' heading; none of the 8 sections."""
        result = self._run(["--change-id", self._change_id, "--by", "tool"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("## By Tool", result.stdout)
        for section in _FEATURE_SECTIONS:
            self.assertNotIn(section, result.stdout, f"Unexpected section: {section}")

    # ------------------------------------------------------------------
    # Scenario 5: --repo default
    # ------------------------------------------------------------------
    def test_repo_default(self):
        """--repo → exit 0, lists change_ids (repo basename match from cwd)."""
        # Run from a path whose basename matches the repo_root basename
        # Our repo_root is /test/myrepo → basename = myrepo
        # We need cwd such that basename(cwd) == 'myrepo'
        fake_cwd = os.path.join(self._tmpdir, "myrepo")
        os.makedirs(fake_cwd, exist_ok=True)
        result = self._run(["--repo"], cwd=fake_cwd)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn(self._change_id, result.stdout)

    # ------------------------------------------------------------------
    # Scenario 6: --repo --by agent
    # ------------------------------------------------------------------
    def test_repo_by_agent(self):
        """--repo --by agent → exit 0, agent aggregation."""
        fake_cwd = os.path.join(self._tmpdir, "myrepo")
        os.makedirs(fake_cwd, exist_ok=True)
        result = self._run(["--repo", "--by", "agent"], cwd=fake_cwd)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("developer", result.stdout)
        self.assertIn("specifier", result.stdout)

    # ------------------------------------------------------------------
    # Scenario 7: --repo --by tool
    # ------------------------------------------------------------------
    def test_repo_by_tool(self):
        """--repo --by tool → exit 0, tool aggregation with is_mcp."""
        fake_cwd = os.path.join(self._tmpdir, "myrepo")
        os.makedirs(fake_cwd, exist_ok=True)
        result = self._run(["--repo", "--by", "tool"], cwd=fake_cwd)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("Bash", result.stdout)

    # ------------------------------------------------------------------
    # Scenario 8: --format json parseability (AC-5)
    # ------------------------------------------------------------------
    def test_format_json_parseable(self):
        """--format json → parseable JSON with 8 documented top-level keys."""
        result = self._run(["--change-id", self._change_id, "--format", "json"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"stdout is not valid JSON: {e}\nstdout: {result.stdout!r}")
        for key in _JSON_REQUIRED_KEYS:
            self.assertIn(key, data, f"Missing JSON key: {key}")

    # ------------------------------------------------------------------
    # Scenario 9: slug-guard rejection (AC-10)
    # ------------------------------------------------------------------
    def test_slug_guard_rejects_invalid_change_id(self):
        """--change-id '../evil' → exit 3, slug-guard message on stderr."""
        result = self._run(["--change-id", "../evil"])
        self.assertEqual(result.returncode, 3, f"stdout: {result.stdout}")
        self.assertIn("slug guard", result.stderr)

    def test_slug_guard_rejects_uppercase(self):
        """--change-id 'Bad-ID' → exit 3."""
        result = self._run(["--change-id", "Bad-ID"])
        self.assertEqual(result.returncode, 3)

    # ------------------------------------------------------------------
    # Scenario 10: byte-identical repeated runs (AC-9)
    # ------------------------------------------------------------------
    def test_byte_identical_repeated_runs(self):
        """Two runs of --change-id X produce byte-identical stdout."""
        result1 = self._run(["--change-id", self._change_id])
        result2 = self._run(["--change-id", self._change_id])
        self.assertEqual(result1.returncode, 0)
        self.assertEqual(result2.returncode, 0)
        self.assertEqual(
            result1.stdout,
            result2.stdout,
            "Repeated runs produced different output (non-deterministic)",
        )

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------
    def test_missing_change_id_and_repo_exits_3(self):
        """Neither --change-id nor --repo → exit 3."""
        result = self._run([])
        self.assertEqual(result.returncode, 3)

    def test_both_change_id_and_repo_exits_3(self):
        """--change-id and --repo together → exit 3."""
        result = self._run(["--change-id", self._change_id, "--repo"])
        self.assertEqual(result.returncode, 3)

    def test_missing_db_exits_3(self):
        """METRICS_DB pointing at non-existent file → exit 3."""
        env = os.environ.copy()
        env["METRICS_DB"] = "/nonexistent/path/to.duckdb"
        env["PYTHONPATH"] = _SCRIPTS_DIR
        env.pop("ORCHESTRATOR_HOME", None)
        result = subprocess.run(
            [sys.executable, _BIN_ORCHESTRATOR, "cost",
             "--change-id", self._change_id],
            capture_output=True,
            text=True,
            env=env,
            cwd=_WORKTREE_ROOT,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("not found", result.stderr)

    def test_since_with_change_id_warns_and_ignores(self):
        """--since with --change-id → warning on stderr, still succeeds."""
        result = self._run([
            "--change-id", self._change_id,
            "--since", "2026-01-01",
        ])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("warning", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
