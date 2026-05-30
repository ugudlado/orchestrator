"""append-retro.sh path resolution in record.py."""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _resolve_append_retro_script, record  # noqa: E402


def _minimal_state(tmp_path, repo_root: str) -> str:
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


class TestAppendRetroPath:
    def test_resolve_prefers_orchestrator_home(self, tmp_path, monkeypatch):
        fake_repo = tmp_path / "repo"
        fake_repo.mkdir()
        fake_home = tmp_path / "home"
        orch = fake_home / "orchestrator_next" / "scripts" / "complete"
        orch.mkdir(parents=True)
        script = orch / "append-retro.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n")
        script.chmod(0o755)

        monkeypatch.setenv("ORCHESTRATOR_HOME", str(fake_home))
        resolved = _resolve_append_retro_script(str(fake_repo))
        assert resolved == str(script)

    def test_resolve_falls_back_to_repo_root(self, tmp_path, monkeypatch):
        fake_repo = tmp_path / "repo"
        orch = fake_repo / "orchestrator_next" / "scripts" / "complete"
        orch.mkdir(parents=True)
        script = orch / "append-retro.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n")
        script.chmod(0o755)
        monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

        resolved = _resolve_append_retro_script(str(fake_repo))
        assert resolved == str(script)

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_record_invokes_orchestrator_next_append_retro(
        self, tmp_path, monkeypatch
    ):
        fake_repo = tmp_path / "repo"
        orch = fake_repo / "orchestrator_next" / "scripts" / "complete"
        orch.mkdir(parents=True)
        script = orch / "append-retro.sh"
        script.write_text(
            '#!/usr/bin/env bash\nprintf \'{"appended": 1, "retro_path": "x"}\'\n'
        )
        script.chmod(0o755)

        state_path = _minimal_state(tmp_path, str(fake_repo))
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            class R:
                returncode = 0
                stdout = '{"appended": 1, "retro_path": "x"}'
                stderr = ""

            return R()

        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

        record(state_path, _completed_payload_with_issues())
        assert calls, "expected subprocess.run for append-retro"
        assert calls[0][0] == "bash"
        assert calls[0][1] == str(script)
