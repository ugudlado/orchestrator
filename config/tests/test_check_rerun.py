"""Tests for the check-rerun workflow step.

check-rerun is the workflow's own decision on whether a finished run reruns.
The load-bearing behavior is the halt: when an archive dir exists, the step
marks every plan node completed + stamps status=completed, so the next
`orchestrator next` finds no ready node and the engine completes the workflow.
These tests exercise that path directly (including a multi-phase plan) rather
than just the dir-presence helper.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# Load the step module directly (it lives in config/steps/, not the package).
_STEP_PY = Path(_REPO) / "config" / "steps" / "check-rerun" / "check_rerun.py"
_spec = importlib.util.spec_from_file_location("check_rerun", _STEP_PY)
check_rerun = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_rerun)


def _archive(repo: Path, dirname: str) -> None:
    (repo / "spec" / "changes" / "archive" / dirname).mkdir(parents=True)


def _seed_state(tmp_path: Path, repo: Path, *, slug: str, phases: dict) -> Path:
    state = {
        "change_id": slug,
        "slug": slug,
        "schema": "feature",
        "status": "active",
        "repo_root": str(repo),
        "phase": next(iter(phases)),
        "ticket_id": "ORC-7",
        "workflow_plan": phases,
        "next_step": {"phase": next(iter(phases)), "step_id": "check-rerun"},
        "step_history": [],
    }
    p = tmp_path / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return p


# --- find_archive_dir (dir-presence helper) -------------------------------

class TestFindArchiveDir:
    def test_finds_by_slug(self, tmp_path):
        repo = tmp_path / "repo"; repo.mkdir()
        _archive(repo, "2026-05-25-orc-7")
        assert check_rerun.find_archive_dir(str(repo), "orc-7") == "spec/changes/archive/2026-05-25-orc-7/"

    def test_finds_by_ticket_via_dir_slug(self, tmp_path):
        repo = tmp_path / "repo"; repo.mkdir()
        _archive(repo, "2026-05-25-orc-7")
        # ticket_id only matches through the dir slug; here slug is empty.
        assert check_rerun.find_archive_dir(str(repo), "", "orc-7") is not None

    def test_missing_returns_none(self, tmp_path):
        repo = tmp_path / "repo"; repo.mkdir()
        assert check_rerun.find_archive_dir(str(repo), "orc-99") is None

    def test_newest_wins(self, tmp_path):
        repo = tmp_path / "repo"; repo.mkdir()
        _archive(repo, "2026-01-01-orc-7")
        _archive(repo, "2026-09-09-orc-7")
        assert "2026-09-09" in check_rerun.find_archive_dir(str(repo), "orc-7")


# --- halt mechanism (the load-bearing behavior) ---------------------------

class TestHaltMechanism:
    def test_single_phase_halts_all_nodes(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"; repo.mkdir()
        _archive(repo, "2026-05-25-orc-7")
        state_path = _seed_state(tmp_path, repo, slug="orc-7", phases={
            "main": {"nodes": [
                {"id": "check-rerun", "status": "pending"},
                {"id": "explore", "status": "pending"},
                {"id": "design", "status": "pending"},
            ], "filtered": []},
        })
        monkeypatch.setenv("STATE_YAML_PATH", str(state_path))
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("CHANGE_ID", "orc-7")

        assert check_rerun.main() == 0
        raw = yaml.safe_load(state_path.read_text())
        assert raw["status"] == "completed"
        assert raw.get("next_step") is None
        assert all(n["status"] == "completed" for n in raw["workflow_plan"]["main"]["nodes"])

    def test_multi_phase_halts_every_phase(self, tmp_path, monkeypatch):
        """Autopilot-style multi-phase plan: ALL phases' nodes must complete,
        so the engine cannot find a ready node in any later phase."""
        repo = tmp_path / "repo"; repo.mkdir()
        _archive(repo, "2026-05-25-orc-7")
        state_path = _seed_state(tmp_path, repo, slug="orc-7", phases={
            "implement": {"nodes": [
                {"id": "check-rerun", "status": "pending"},
                {"id": "explore", "status": "pending"},
            ], "filtered": []},
            "complete": {"nodes": [
                {"id": "learn", "status": "pending"},
                {"id": "ticket-done", "status": "pending"},
            ], "filtered": []},
        })
        monkeypatch.setenv("STATE_YAML_PATH", str(state_path))
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("CHANGE_ID", "orc-7")

        assert check_rerun.main() == 0
        raw = yaml.safe_load(state_path.read_text())
        assert raw["status"] == "completed"
        for phase in ("implement", "complete"):
            assert all(
                n["status"] == "completed"
                for n in raw["workflow_plan"][phase]["nodes"]
            ), f"phase {phase} has an incomplete node — engine would still dispatch it"

    def test_proceed_when_no_archive_leaves_state_untouched(self, tmp_path, monkeypatch):
        """Proceed path writes NOTHING to state — the engine marks the inline
        node completed on exit 0 (bin/orchestrator records it). The script must
        not flip status or any node itself."""
        repo = tmp_path / "repo"; repo.mkdir()  # no archive dir
        state_path = _seed_state(tmp_path, repo, slug="orc-7", phases={
            "main": {"nodes": [
                {"id": "check-rerun", "status": "pending"},
                {"id": "explore", "status": "pending"},
            ], "filtered": []},
        })
        before = state_path.read_text()
        monkeypatch.setenv("STATE_YAML_PATH", str(state_path))
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("CHANGE_ID", "orc-7")

        assert check_rerun.main() == 0
        assert state_path.read_text() == before, "proceed path must not write state.yaml"
