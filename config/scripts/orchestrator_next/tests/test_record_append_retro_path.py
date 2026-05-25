"""T-21: Failing test for append-retro.sh path resolution in record.py.

Bug (AC-11): record.py builds the append-retro.sh path as
  <repo_root>/scripts/inline/append-retro.sh
The canonical location is:
  <repo_root>/config/scripts/inline/append-retro.sh

This test verifies the correct path is used and will fail until T-22 fixes record.py.
"""
from __future__ import annotations

import os
import sys
import json

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import record  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_state(tmp_path, repo_root: str) -> str:
    """Write a minimal valid state.yaml to tmp_path and return its path."""
    state = {
        "change_id": "test-retro",
        "phase": "implement",
        "repo_root": repo_root,
        "worktree_path": repo_root,
        "schema": "feature",
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {
                        "id": "explore",
                        "status": "in_progress",
                        "agent": "discoverer",
                        "goal": "Explore",
                        "inputs": [],
                        "outputs": [],
                        "rules": [],
                    }
                ],
                "filtered": [],
            }
        },
        "step_history": [
            {
                "step_id": "explore",
                "phase": "implement",
                "status": "in_progress",
                "evidence": {"outputs": {}},
            }
        ],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _completed_payload_with_issues() -> dict:
    return {
        "step_id": "explore",
        "phase": "implement",
        "status": "completed",
        "agent": "discoverer",
        "outputs": {},
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "workflow_issues": [
            {"kind": "test_issue", "detail": "some detail"},
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAppendRetroPath:
    """AC-11: record.py must invoke append-retro.sh at config/scripts/inline/."""

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Point contract search at empty dir — no contract validation runs."""
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_append_retro_uses_config_prefix_path(self, tmp_path, monkeypatch):
        """
        RED (T-21): When workflow_issues is present, record.py must look up
        append-retro.sh at <repo_root>/config/scripts/inline/append-retro.sh.

        Currently FAILS because record.py uses <repo_root>/scripts/inline/append-retro.sh
        (missing the config/ prefix). T-22 will fix this.
        """
        # Build a fake repo root with the script at the CORRECT path
        fake_repo = tmp_path / "fakerepo"
        correct_dir = fake_repo / "config" / "scripts" / "inline"
        correct_dir.mkdir(parents=True)
        correct_script = correct_dir / "append-retro.sh"
        # Write a minimal script that outputs valid JSON
        correct_script.write_text('#!/bin/bash\necho \'{"appended": 1}\'\n')
        correct_script.chmod(0o755)

        # Do NOT create the stale path (<repo_root>/scripts/inline/append-retro.sh)
        # so that if record.py tries the wrong path, the script won't be found.

        state_path = _minimal_state(tmp_path, str(fake_repo))

        # Track subprocess.run calls
        captured_calls = []

        import subprocess as _sp_module

        original_run = _sp_module.run

        def mock_run(cmd, **kwargs):
            captured_calls.append(cmd)
            # Execute the real script so record.py doesn't error
            return original_run(cmd, **kwargs)

        monkeypatch.setattr(_sp_module, "run", mock_run)

        # Also patch the subprocess imported inside record.py's local scope
        # record.py does `import subprocess as _sp` inside the function body,
        # so we patch via sys.modules
        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)

        payload = _completed_payload_with_issues()
        _result, _exit_code = record(state_path, payload)

        # The test asserts: subprocess was called with the correct path
        assert len(captured_calls) >= 1, (
            "Expected subprocess.run to be called for append-retro.sh, "
            "but it was never called. The script was not found — "
            "record.py likely used the wrong (stale) path."
        )

        invoked_script_path = captured_calls[0][1]  # ["bash", "<script>"]
        correct_path = str(fake_repo / "config" / "scripts" / "inline" / "append-retro.sh")
        stale_path = str(fake_repo / "scripts" / "inline" / "append-retro.sh")

        assert invoked_script_path == correct_path, (
            f"record.py invoked append-retro.sh at the WRONG path.\n"
            f"  Got:      {invoked_script_path}\n"
            f"  Expected: {correct_path}\n"
            f"  The stale path (missing 'config/' prefix) is: {stale_path}"
        )
        assert invoked_script_path != stale_path, (
            f"record.py used the stale path {stale_path!r} — missing 'config/' prefix."
        )

    def test_stale_path_is_not_used(self, tmp_path, monkeypatch):
        """
        RED (T-21): Complementary assertion — if only the stale path exists,
        record.py must NOT invoke the script (because it should be looking at
        the correct config/ path which doesn't exist in this scenario).

        Currently FAILS because record.py uses the stale path and would
        successfully find and invoke it here.
        """
        # Build a fake repo root with the script at the STALE (wrong) path only
        fake_repo = tmp_path / "fakerepo2"
        stale_dir = fake_repo / "scripts" / "inline"
        stale_dir.mkdir(parents=True)
        stale_script = stale_dir / "append-retro.sh"
        stale_script.write_text('#!/bin/bash\necho \'{"appended": 1}\'\n')
        stale_script.chmod(0o755)

        # Do NOT create the correct path (config/scripts/inline/append-retro.sh)

        state_path = _minimal_state(tmp_path, str(fake_repo))

        captured_calls = []

        import subprocess as _sp_module

        original_run = _sp_module.run

        def mock_run(cmd, **kwargs):
            captured_calls.append(cmd)
            return original_run(cmd, **kwargs)

        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(_sp_module, "run", mock_run)
        # Unset ORCHESTRATOR_HOME so the fallback path lookup is also isolated
        monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

        payload = _completed_payload_with_issues()
        _result, _exit_code = record(state_path, payload)

        # With only the stale path present, subprocess.run should NOT be called
        # (the script at the correct path doesn't exist, so record.py should skip it).
        # Currently FAILS: record.py finds the stale path and invokes it.
        assert len(captured_calls) == 0, (
            f"record.py invoked subprocess.run using the stale path.\n"
            f"  Called with: {captured_calls}\n"
            f"  The stale path {str(stale_script)!r} should NOT be looked up.\n"
            f"  Only config/scripts/inline/append-retro.sh is canonical."
        )
