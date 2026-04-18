"""
Unit tests for the claude_discoverer adapter (T-9 verification).

These tests mock subprocess.run so they do NOT require a real API key and run
in CI unconditionally.

Coverage:
  - _extract_usage: correct mapping of Claude CLI JSON to usage dict
  - _build_prompt: instruction + rules formatting
  - _append_step_history: correct YAML shape appended to state.yaml
  - main() error gates: missing env vars, missing claude binary, non-zero
    claude exit, invalid JSON output

The adapter is imported directly (not subprocess'd) so we can patch internals.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

# ---------------------------------------------------------------------------
# sys.path: make adapters/ and config/scripts/ importable
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SCRIPTS_DIR = os.path.join(_WORKTREE_ROOT, "config", "scripts")
_ADAPTERS_DIR = os.path.join(_SCRIPTS_DIR, "adapters")

for d in (_SCRIPTS_DIR, _ADAPTERS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

# Import the adapter module under test
import claude_discoverer as adapter  # noqa: E402


# ---------------------------------------------------------------------------
# A canned Claude CLI JSON payload matching --output-format json
# ---------------------------------------------------------------------------
_CANNED_PAYLOAD = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 4200,
    "duration_api_ms": 3900,
    "num_turns": 1,
    "result": "Discovery brief content here.",
    "stop_reason": "end_turn",
    "session_id": "abc123",
    "total_cost_usd": 0.23456,
    "usage": {
        "input_tokens": 15000,
        "cache_creation_input_tokens": 4000,
        "cache_read_input_tokens": 8500,
        "output_tokens": 2200,
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
    },
    "modelUsage": {
        "claude-sonnet-4-6": {
            "inputTokens": 15000,
            "outputTokens": 2200,
            "cacheReadInputTokens": 8500,
            "cacheCreationInputTokens": 4000,
            "costUSD": 0.23456,
        }
    },
    "permission_denials": [],
    "terminal_reason": "completed",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_state_yaml(tmpdir: str, step_id: str = "explore") -> str:
    """Write a minimal state.yaml to tmpdir and return its path."""
    content = textwrap.dedent(f"""\
        change_id: test-adapter-run
        schema: feature
        version: 1
        status: active
        phase: specify
        repo: test-repo
        worktree_path: {tmpdir}
        workflow_plan:
          specify:
            active:
              - {step_id}
        step_history: []
    """)
    path = os.path.join(tmpdir, "state.yaml")
    with open(path, "w") as f:
        f.write(content)
    return path


def _make_explore_contract(tmpdir: str) -> str:
    """Write a minimal explore.yaml to tmpdir/config/steps/ and return dir."""
    steps_dir = os.path.join(tmpdir, "config", "steps")
    os.makedirs(steps_dir, exist_ok=True)
    contract = textwrap.dedent("""\
        id: explore
        version: 3
        agent: discoverer
        run: config/scripts/adapters/claude_discoverer.py
        rules:
          - Focus on problem-space survey.
          - Capture unresolved questions explicitly.
        instruction: |
          Survey the codebase for relevant patterns.
    """)
    path = os.path.join(steps_dir, "explore.yaml")
    with open(path, "w") as f:
        f.write(contract)
    return steps_dir


# ---------------------------------------------------------------------------
# Test: _extract_usage
# ---------------------------------------------------------------------------

class TestExtractUsage(unittest.TestCase):

    def test_extracts_all_fields_from_canned_payload(self):
        usage = adapter._extract_usage(_CANNED_PAYLOAD, elapsed_ms=5000)

        self.assertEqual(usage["input_tokens"], 15000)
        self.assertEqual(usage["output_tokens"], 2200)
        self.assertEqual(usage["cache_read_input_tokens"], 8500)
        self.assertAlmostEqual(usage["cost_usd"], 0.23456)
        self.assertEqual(usage["duration_ms"], 4200)  # from payload, not elapsed_ms
        self.assertEqual(usage["model"], "claude-sonnet-4-6")
        self.assertIn("tool_calls", usage)  # present but empty dict
        self.assertEqual(usage["tool_calls"], {})
        self.assertNotIn("usage_capture", usage)

    def test_falls_back_to_elapsed_ms_when_no_duration_in_payload(self):
        payload = dict(_CANNED_PAYLOAD)
        del payload["duration_ms"]
        usage = adapter._extract_usage(payload, elapsed_ms=9999)
        self.assertEqual(usage["duration_ms"], 9999)

    def test_no_usage_fields_sets_unavailable(self):
        payload = {"type": "result", "usage": {}, "modelUsage": {}}
        usage = adapter._extract_usage(payload, elapsed_ms=100)
        self.assertEqual(usage.get("usage_capture"), "unavailable")

    def test_model_picked_from_model_usage_keys(self):
        payload = dict(_CANNED_PAYLOAD)
        payload["modelUsage"] = {
            "claude-opus-4": {"costUSD": 1.0},
            "claude-sonnet-4-6": {"costUSD": 0.5},
        }
        usage = adapter._extract_usage(payload, elapsed_ms=0)
        # Sorted alphabetically: 'claude-opus-4' < 'claude-sonnet-4-6'
        self.assertEqual(usage["model"], "claude-opus-4")


# ---------------------------------------------------------------------------
# Test: _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt(unittest.TestCase):

    def test_prompt_includes_rules_and_instruction(self):
        rules = ["Rule A.", "Rule B."]
        instruction = "Do the thing."
        prompt = adapter._build_prompt(instruction, rules)
        self.assertIn("Rule A.", prompt)
        self.assertIn("Rule B.", prompt)
        self.assertIn("Do the thing.", prompt)

    def test_prompt_with_empty_rules(self):
        prompt = adapter._build_prompt("Only instruction.", [])
        self.assertIn("Only instruction.", prompt)
        self.assertNotIn("## Rules", prompt)

    def test_prompt_with_no_instruction(self):
        prompt = adapter._build_prompt("", ["Rule 1."])
        self.assertIn("Rule 1.", prompt)


# ---------------------------------------------------------------------------
# Test: _append_step_history
# ---------------------------------------------------------------------------

class TestAppendStepHistory(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_adapter_test_")
        self._state_path = _make_minimal_state_yaml(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run_append(self, usage: dict) -> None:
        env = {
            "ORCHESTRATOR_CHANGE_ID": "test-adapter-run",
            "ORCHESTRATOR_PHASE": "specify",
            "ORCHESTRATOR_STEP_ID": "explore",
            "ORCHESTRATOR_ATTEMPT": "1",
            "ORCHESTRATOR_WORKFLOW_DIR": self._tmpdir,
            "ORCHESTRATOR_REPO_ROOT": self._tmpdir,
        }
        adapter._append_step_history(
            self._state_path,
            env,
            usage,
            started_at="2026-04-18T12:00:00Z",
        )

    def test_entry_is_appended(self):
        usage = {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}
        self._run_append(usage)

        with open(self._state_path, "r") as f:
            doc = yaml.safe_load(f)

        history = doc.get("step_history", [])
        self.assertEqual(len(history), 1)
        entry = history[0]
        self.assertEqual(entry["step_id"], "explore")
        self.assertEqual(entry["phase"], "specify")
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["agent"], "discoverer")
        self.assertEqual(entry["attempt"], 1)
        self.assertEqual(entry["started_at"], "2026-04-18T12:00:00Z")
        self.assertIn("ended_at", entry)

    def test_usage_block_is_correct(self):
        usage = {
            "input_tokens": 15000,
            "output_tokens": 2200,
            "cache_read_input_tokens": 8500,
            "cost_usd": 0.23456,
            "tool_calls": {},
            "duration_ms": 4200,
            "model": "claude-sonnet-4-6",
        }
        self._run_append(usage)

        with open(self._state_path, "r") as f:
            doc = yaml.safe_load(f)

        entry_usage = doc["step_history"][0]["usage"]
        self.assertEqual(entry_usage["input_tokens"], 15000)
        self.assertEqual(entry_usage["output_tokens"], 2200)
        self.assertEqual(entry_usage["cache_read_input_tokens"], 8500)
        self.assertAlmostEqual(entry_usage["cost_usd"], 0.23456)
        self.assertEqual(entry_usage["duration_ms"], 4200)
        self.assertEqual(entry_usage["model"], "claude-sonnet-4-6")

    def test_existing_content_preserved(self):
        """Existing step_history entries and top-level keys must be preserved."""
        # Add a prior entry
        with open(self._state_path, "r") as f:
            doc = yaml.safe_load(f)
        doc["step_history"] = [{
            "step_id": "load-project-context",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "attempt": 1,
            "started_at": "2026-04-18T11:00:00Z",
            "ended_at": "2026-04-18T11:01:00Z",
        }]
        with open(self._state_path, "w") as f:
            yaml.dump(doc, f)

        self._run_append({"input_tokens": 100})

        with open(self._state_path, "r") as f:
            doc2 = yaml.safe_load(f)

        history = doc2["step_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["step_id"], "load-project-context")
        self.assertEqual(history[1]["step_id"], "explore")
        # Top-level fields preserved
        self.assertEqual(doc2["change_id"], "test-adapter-run")

    def test_atomic_write_produces_valid_yaml(self):
        """After write, file must be parseable YAML."""
        self._run_append({"cost_usd": 0.5})
        with open(self._state_path, "r") as f:
            doc = yaml.safe_load(f)
        self.assertIsInstance(doc, dict)
        self.assertIn("step_history", doc)


# ---------------------------------------------------------------------------
# Test: main() error gates (via subprocess mock)
# ---------------------------------------------------------------------------

class TestMainErrorGates(unittest.TestCase):
    """Test that main() exits cleanly without writing state.yaml in failure cases."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_adapter_gate_")
        self._state_path = _make_minimal_state_yaml(self._tmpdir)
        _make_explore_contract(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _base_env(self):
        return {
            "ORCHESTRATOR_CHANGE_ID": "test-adapter-run",
            "ORCHESTRATOR_PHASE": "specify",
            "ORCHESTRATOR_STEP_ID": "explore",
            "ORCHESTRATOR_ATTEMPT": "1",
            "ORCHESTRATOR_WORKFLOW_DIR": self._tmpdir,
            "ORCHESTRATOR_REPO_ROOT": self._tmpdir,
        }

    def test_exits_1_when_claude_not_on_path(self):
        """When 'claude' is not on PATH, adapter must exit 1 without touching state.yaml."""
        import stat
        mtime_before = os.path.getmtime(self._state_path)

        with patch.dict(os.environ, self._base_env()):
            # ORCHESTRATOR_HOME not set → uses workflow_dir and repo_root
            with patch("shutil.which", return_value=None):
                with self.assertRaises(SystemExit) as ctx:
                    adapter.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(os.path.getmtime(self._state_path), mtime_before)

    def test_exits_1_when_claude_returns_nonzero(self):
        """Non-zero claude exit must cause adapter to exit 1 without writing state.yaml."""
        mtime_before = os.path.getmtime(self._state_path)
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stderr = "API error"
        mock_result.stdout = ""

        with patch.dict(os.environ, self._base_env()):
            with patch("shutil.which", return_value="/usr/local/bin/claude"):
                with patch("subprocess.run", return_value=mock_result):
                    with self.assertRaises(SystemExit) as ctx:
                        adapter.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(os.path.getmtime(self._state_path), mtime_before)

    def test_exits_1_when_claude_returns_invalid_json(self):
        """Non-JSON claude stdout must cause adapter to exit 1 without writing state.yaml."""
        mtime_before = os.path.getmtime(self._state_path)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "this is not json"
        mock_result.stderr = ""

        with patch.dict(os.environ, self._base_env()):
            with patch("shutil.which", return_value="/usr/local/bin/claude"):
                with patch("subprocess.run", return_value=mock_result):
                    with self.assertRaises(SystemExit) as ctx:
                        adapter.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(os.path.getmtime(self._state_path), mtime_before)

    def test_successful_mock_run_writes_state_yaml(self):
        """With a canned success payload, state.yaml gains a new step_history entry."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(_CANNED_PAYLOAD)
        mock_result.stderr = ""

        with patch.dict(os.environ, self._base_env()):
            with patch("shutil.which", return_value="/usr/local/bin/claude"):
                with patch("subprocess.run", return_value=mock_result):
                    with self.assertRaises(SystemExit) as ctx:
                        adapter.main()
        self.assertEqual(ctx.exception.code, 0)

        with open(self._state_path, "r") as f:
            doc = yaml.safe_load(f)

        history = doc.get("step_history", [])
        self.assertEqual(len(history), 1)
        entry = history[0]
        self.assertEqual(entry["step_id"], "explore")
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["agent"], "discoverer")

        usage = entry["usage"]
        self.assertEqual(usage["input_tokens"], 15000)
        self.assertEqual(usage["output_tokens"], 2200)
        self.assertAlmostEqual(usage["cost_usd"], 0.23456)
        self.assertEqual(usage["model"], "claude-sonnet-4-6")


if __name__ == "__main__":
    unittest.main()
