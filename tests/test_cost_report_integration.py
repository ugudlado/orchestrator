"""
Integration tests for cost_report (T-5).

Seeds a tempfile DuckDB with step_events + tool_calls rows spanning:
  - Two change_ids sharing the same basename(repo_root)
  - Multiple agents, phases, models, tool types (native + MCP)
  - A fake agent frontmatter file in a tempdir used as ORCHESTRATOR_HOME

Assertions (from tasks.md T-5):
  (a) feature-level report contains all eight section headings in order (AC-2)
  (b) --by step|agent|tool each produce a single table with no extra sections (AC-3)
  (c) --repo lists both features; --repo --by agent aggregates across both (AC-4)
  (d) --format json parses and has the eight documented top-level keys (AC-5)
  (e) native vs MCP split is correct by is_mcp column (AC-6)
  (f) anomaly row emitted for (developer, Bash) when frontmatter only declares ["Read", "Edit"];
      no row when agent file absent (AC-7)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_SCRIPTS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")
_BIN_ORCHESTRATOR = os.path.join(ORCHESTRATOR_ROOT, "bin", "orchestrator")

if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_FEATURE_SECTIONS_ORDERED = [
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


def _seed_fixture_db(db_path: str, repo_root: str) -> None:
    """
    Seed two change_ids:
      - 'feature-alpha': specifier (specify phase, native tools only)
                         developer (implement phase, mixed tools)
      - 'feature-beta':  developer (implement phase, MCP only)
    Both share the same repo_root (same basename).
    """
    import duckdb
    from orchestrator_next.upsert import ensure_schema, upsert_step_event
    from orchestrator_next.parser import StepHistoryEntry

    db = duckdb.connect(db_path)
    ensure_schema(db)

    # feature-alpha
    entries_alpha = [
        StepHistoryEntry(
            step_id="specify-1",
            phase="specify",
            status="completed",
            agent="specifier",
            attempt=1,
            started_at="2026-04-10T10:00:00Z",
            ended_at="2026-04-10T10:20:00Z",
            usage={
                "input_tokens": 3000,
                "output_tokens": 400,
                "cost_usd": 0.05,
                "duration_ms": 20000,
                "model": "claude-sonnet-4-5",
                "tool_calls": {"Read": 4, "Grep": 2},
            },
            escalation=None,
            raw={},
        ),
        StepHistoryEntry(
            step_id="implement-1",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-04-10T10:21:00Z",
            ended_at="2026-04-10T11:00:00Z",
            usage={
                "input_tokens": 10000,
                "output_tokens": 1500,
                "cost_usd": 0.40,
                "duration_ms": 80000,
                "model": "claude-sonnet-4-5",
                # Bash is an anomaly for developer (not in declared tools)
                "tool_calls": {"Bash": 3, "Read": 5, "mcp__pal__thinkdeep": 2},
            },
            escalation=None,
            raw={},
        ),
        # Rework row (attempt=2) — contributes to rework ratio
        StepHistoryEntry(
            step_id="implement-1",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=2,
            started_at="2026-04-10T11:01:00Z",
            ended_at="2026-04-10T11:30:00Z",
            usage={
                "input_tokens": 5000,
                "output_tokens": 700,
                "cost_usd": 0.15,
                "duration_ms": 40000,
                "model": "claude-opus-4-5",
                "tool_calls": {"Edit": 4, "mcp__pal__thinkdeep": 1},
            },
            escalation=None,
            raw={},
        ),
    ]
    ctx_alpha = {"repo_root": repo_root, "change_id": "feature-alpha"}
    for e in entries_alpha:
        upsert_step_event(db, e, ctx_alpha)

    # feature-beta
    entries_beta = [
        StepHistoryEntry(
            step_id="implement-beta",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-04-15T09:00:00Z",
            ended_at="2026-04-15T09:45:00Z",
            usage={
                "input_tokens": 8000,
                "output_tokens": 1200,
                "cost_usd": 0.30,
                "duration_ms": 60000,
                "model": "claude-sonnet-4-5",
                "tool_calls": {"mcp__pal__think": 3, "mcp__github__search": 1},
            },
            escalation=None,
            raw={},
        ),
    ]
    ctx_beta = {"repo_root": repo_root, "change_id": "feature-beta"}
    for e in entries_beta:
        upsert_step_event(db, e, ctx_beta)

    db.close()


def _make_agent_frontmatter(agents_dir: str) -> None:
    """Write a developer.md with frontmatter tools: ["Read", "Edit"] (no Bash)."""
    os.makedirs(agents_dir, exist_ok=True)
    content = """---
name: developer
description: Developer agent
tools:
  - Read
  - Edit
---

# Developer Agent
"""
    with open(os.path.join(agents_dir, "developer.md"), "w") as f:
        f.write(content)


def _run_cost(
    args: list[str],
    db_path: str,
    orchestrator_home: str | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["METRICS_DB"] = db_path
    env["PYTHONPATH"] = _SCRIPTS_DIR
    if orchestrator_home:
        env["ORCHESTRATOR_HOME"] = orchestrator_home
    else:
        env.pop("ORCHESTRATOR_HOME", None)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "cost"] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or ORCHESTRATOR_ROOT,
    )


class TestCostReportIntegration(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_integration_test_")
        self._db_path = os.path.join(self._tmpdir, "test.duckdb")
        # repo_root basename is "myproject"
        self._repo_root = "/home/dev/myproject"
        self._repo_basename = "myproject"

        # Create fake cwd with matching basename
        self._fake_cwd = os.path.join(self._tmpdir, self._repo_basename)
        os.makedirs(self._fake_cwd, exist_ok=True)

        # ORCHESTRATOR_HOME with developer.md frontmatter
        self._orch_home = os.path.join(self._tmpdir, "orchestrator_home")
        _make_agent_frontmatter(os.path.join(self._orch_home, "agents"))

        _seed_fixture_db(self._db_path, self._repo_root)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, args: list[str], with_orch_home: bool = True) -> subprocess.CompletedProcess:
        return _run_cost(
            args,
            self._db_path,
            orchestrator_home=self._orch_home if with_orch_home else None,
            cwd=self._fake_cwd,
        )

    # ------------------------------------------------------------------
    # (a) AC-2: all eight section headings in order
    # ------------------------------------------------------------------
    def test_feature_report_has_eight_sections_in_order(self):
        """feature-alpha report has all 8 sections in the documented order."""
        result = self._run(["--change-id", "feature-alpha"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        stdout = result.stdout

        last_pos = -1
        for section in _FEATURE_SECTIONS_ORDERED:
            pos = stdout.find(section)
            self.assertGreater(
                pos, -1,
                f"Section '{section}' not found in output.\nOutput:\n{stdout}",
            )
            self.assertGreater(
                pos, last_pos,
                f"Section '{section}' appears before expected position (ordering violated).",
            )
            last_pos = pos

    # ------------------------------------------------------------------
    # (b) AC-3: --by step|agent|tool produce single table only
    # ------------------------------------------------------------------
    def test_by_step_no_extra_sections(self):
        """--by step → ## By Step only, no feature sections."""
        result = self._run(["--change-id", "feature-alpha", "--by", "step"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("## By Step", result.stdout)
        for section in _FEATURE_SECTIONS_ORDERED:
            self.assertNotIn(section, result.stdout)

    def test_by_agent_no_extra_sections(self):
        """--by agent → ## By Agent only."""
        result = self._run(["--change-id", "feature-alpha", "--by", "agent"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("## By Agent", result.stdout)
        for section in _FEATURE_SECTIONS_ORDERED:
            self.assertNotIn(section, result.stdout)

    def test_by_tool_no_extra_sections(self):
        """--by tool → ## By Tool only."""
        result = self._run(["--change-id", "feature-alpha", "--by", "tool"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("## By Tool", result.stdout)
        for section in _FEATURE_SECTIONS_ORDERED:
            self.assertNotIn(section, result.stdout)

    # ------------------------------------------------------------------
    # (c) AC-4: --repo lists both features; --repo --by agent aggregates
    # ------------------------------------------------------------------
    def test_repo_lists_both_features(self):
        """--repo lists feature-alpha and feature-beta."""
        result = self._run(["--repo"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("feature-alpha", result.stdout)
        self.assertIn("feature-beta", result.stdout)

    def test_repo_by_agent_aggregates_across_both(self):
        """--repo --by agent aggregates developer rows from both features."""
        result = self._run(["--repo", "--by", "agent"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # developer appears in both features — should be in aggregate
        self.assertIn("developer", result.stdout)
        # specifier only in feature-alpha
        self.assertIn("specifier", result.stdout)

    # ------------------------------------------------------------------
    # (d) AC-5: --format json has 8 top-level keys
    # ------------------------------------------------------------------
    def test_format_json_has_eight_keys(self):
        """--format json → parseable JSON with 8 top-level keys."""
        result = self._run(["--change-id", "feature-alpha", "--format", "json"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"Output is not valid JSON: {e}\nstdout: {result.stdout!r}")
        for key in _JSON_REQUIRED_KEYS:
            self.assertIn(key, data, f"Missing JSON key: {key}")

    # ------------------------------------------------------------------
    # (e) AC-6: native vs MCP split correct by is_mcp column
    # ------------------------------------------------------------------
    def test_native_tools_section_contains_native_tools(self):
        """## Native Tools contains Bash/Read/Grep/Edit (is_mcp=false)."""
        result = self._run(["--change-id", "feature-alpha"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Find the native tools section content
        native_pos = result.stdout.find("## Native Tools")
        mcp_pos = result.stdout.find("## MCP Calls")
        native_section = result.stdout[native_pos:mcp_pos]
        # Should contain native tool names
        self.assertIn("Read", native_section)
        self.assertIn("Bash", native_section)
        # Should NOT contain MCP tool names
        self.assertNotIn("mcp__", native_section)

    def test_mcp_calls_section_contains_mcp_tools(self):
        """## MCP Calls contains mcp__ tools (is_mcp=true)."""
        result = self._run(["--change-id", "feature-alpha"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        mcp_pos = result.stdout.find("## MCP Calls")
        per_agent_pos = result.stdout.find("## Per-Agent Tool Use")
        mcp_section = result.stdout[mcp_pos:per_agent_pos]
        self.assertIn("mcp__pal__thinkdeep", mcp_section)
        # Should NOT contain non-MCP tool names
        self.assertNotIn("Read", mcp_section)
        self.assertNotIn("Bash", mcp_section)

    def test_json_native_mcp_split(self):
        """JSON output has native_tools and mcp_calls with correct items."""
        result = self._run(["--change-id", "feature-alpha", "--format", "json"])
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        data = json.loads(result.stdout)

        native_tools = {r["tool_name"]: r for r in data["native_tools"]}
        mcp_calls = {r["tool_name"]: r for r in data["mcp_calls"]}

        self.assertIn("Bash", native_tools)
        self.assertIn("Read", native_tools)
        self.assertIn("mcp__pal__thinkdeep", mcp_calls)
        # No overlap
        for tool in native_tools:
            self.assertNotIn(tool, mcp_calls, f"Tool {tool} in both native and MCP")

    # ------------------------------------------------------------------
    # (f) AC-7: anomaly detection
    # ------------------------------------------------------------------
    def test_anomaly_emitted_for_undeclared_tool(self):
        """Anomaly for developer using Bash (not in ['Read', 'Edit'])."""
        result = self._run(["--change-id", "feature-alpha"], with_orch_home=True)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        anomaly_pos = result.stdout.find("## Anomalies")
        anomaly_section = result.stdout[anomaly_pos:]
        self.assertIn("developer", anomaly_section)
        self.assertIn("Bash", anomaly_section)

    def test_no_anomaly_when_agent_file_absent(self):
        """No anomaly row when agent file is absent (specifier has no .md)."""
        result = self._run(["--change-id", "feature-alpha"], with_orch_home=True)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        anomaly_pos = result.stdout.find("## Anomalies")
        anomaly_section = result.stdout[anomaly_pos:]
        # specifier has no agent file → should not appear in anomalies
        self.assertNotIn("specifier", anomaly_section)

    def test_no_anomaly_when_agent_file_absent_for_specifier(self):
        """
        specifier has no agent file in orch_home → no anomaly row for specifier,
        regardless of what tools specifier used.
        """
        result = self._run(["--change-id", "feature-alpha"], with_orch_home=True)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        anomaly_pos = result.stdout.find("## Anomalies")
        anomaly_section = result.stdout[anomaly_pos:]
        # specifier has no agent file → never appears in anomalies
        self.assertNotIn("specifier", anomaly_section)

    # ------------------------------------------------------------------
    # Additional correctness
    # ------------------------------------------------------------------
    def test_totals_sum_correct(self):
        """JSON totals cost_usd is sum of all step events for feature-alpha."""
        result = self._run(["--change-id", "feature-alpha", "--format", "json"])
        data = json.loads(result.stdout)
        # feature-alpha: 0.05 + 0.40 + 0.15 = 0.60
        self.assertAlmostEqual(data["totals"]["cost_usd"], 0.60, places=4)

    def test_rework_ratio_nonzero(self):
        """Rework ratio > 0 when there's an attempt=2 row."""
        result = self._run(["--change-id", "feature-alpha", "--format", "json"])
        data = json.loads(result.stdout)
        # rework cost = 0.15 (attempt=2), total = 0.60
        expected = 0.15 / 0.60
        self.assertAlmostEqual(data["totals"]["rework_ratio"], expected, places=4)

    def test_per_phase_ordering(self):
        """per_phase is ordered by first_seen: specify before implement."""
        result = self._run(["--change-id", "feature-alpha", "--format", "json"])
        data = json.loads(result.stdout)
        phases = [r["phase"] for r in data["per_phase"]]
        self.assertEqual(phases.index("specify"), 0)
        self.assertEqual(phases.index("implement"), 1)

    def test_feature_beta_mcp_only(self):
        """feature-beta has MCP calls and no native tools."""
        result = self._run(["--change-id", "feature-beta", "--format", "json"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["native_tools"]), 0)
        self.assertGreater(len(data["mcp_calls"]), 0)


if __name__ == "__main__":
    unittest.main()
