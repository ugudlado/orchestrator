"""Tests for archive_completion rerun short-circuit."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.archive_completion import (  # noqa: E402
    find_completed_archive,
    finalize_already_completed_rerun,
    handle,
    probe,
)


def _write_archive(repo: Path, dirname: str, *, change_id: str, ticket_id: str) -> Path:
    arch_dir = repo / "spec" / "changes" / "archive" / dirname
    arch_dir.mkdir(parents=True)
    state = {
        "change_id": change_id,
        "slug": change_id,
        "status": "completed",
        "ticket_id": ticket_id,
        "completed_at": "2026-05-25T17:21:55Z",
        "archive_path": f"spec/changes/archive/{dirname}/",
        "step_history": [
            {"step_id": "mark-change-completed", "status": "completed", "phase": "main"},
        ],
    }
    path = arch_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return path


def _minimal_active_state(tmp_path: Path, repo: Path, slug: str) -> Path:
    change_dir = tmp_path / "wt" / "spec" / "changes" / slug
    change_dir.mkdir(parents=True)
    state_path = change_dir / "state.yaml"
    state = {
        "change_id": slug,
        "slug": slug,
        "schema": "feature",
        "status": "active",
        "repo_root": str(repo),
        "worktree_path": str(tmp_path / "wt"),
        "phase": "main",
        "ticket_id": "ORC-7",
        "flags": {"worktree": True},
        "workflow_plan": {
            "main": {
                "nodes": [
                    {"id": "explore", "status": "pending"},
                    {"id": "design-and-draft-artifacts", "status": "pending"},
                ],
                "filtered": [],
            }
        },
        "next_step": {"phase": "main", "step_id": "explore"},
        "step_history": [],
    }
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    (repo / "spec" / "project.yaml").write_text("name: test\n")
    return state_path


class TestFindCompletedArchive:
    def test_finds_by_slug(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_archive(repo, "2026-05-25-orc-7", change_id="orc-7", ticket_id="ORC-7")
        found = find_completed_archive(repo, slug="orc-7")
        assert found is not None
        assert "2026-05-25-orc-7" in found["archive_path"]

    def test_finds_by_ticket(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_archive(repo, "2026-05-25-orc-7", change_id="orc-7", ticket_id="ORC-7")
        found = find_completed_archive(repo, ticket_id="ORC-7")
        assert found is not None

    def test_missing_returns_none(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert find_completed_archive(repo, slug="orc-99") is None


class TestProbe:
    def test_halt_when_archived(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_archive(repo, "2026-05-25-orc-7", change_id="orc-7", ticket_id="ORC-7")
        out = probe(str(repo), "orc-7", "ORC-7")
        assert out["action"] == "halt_complete"
        assert "archive" in out["message"]


class TestHandle:
    def test_finalize_active_rerun(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_archive(repo, "2026-05-25-orc-7", change_id="orc-7", ticket_id="ORC-7")
        state_path = _minimal_active_state(tmp_path, repo, "orc-7")

        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        for step in ("explore", "design-and-draft-artifacts"):
            d = empty / step
            d.mkdir()
            (d / "contract.yaml").write_text(
                yaml.safe_dump(
                    {
                        "id": step,
                        "kind": "agent",
                        "agent": "discoverer" if step == "explore" else "architect",
                        "inputs": [],
                        "outputs": (
                            ["discovery_result", {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"}]
                            if step == "explore"
                            else [
                                "updated_artifact_set",
                                "design_direction",
                                "complexity",
                                {"name": "design", "path": "spec/changes/<slug>/design.md"},
                                {"name": "tasks", "path": "spec/changes/<slug>/tasks.yaml"},
                            ]
                        ),
                    }
                )
            )
            (d / "prompt.md").write_text("# test\n")

        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

        out = handle(str(state_path))
        assert out["action"] == "halt_complete"
        assert out["flagged_by"] == "discoverer"

        raw = yaml.safe_load(state_path.read_text())
        assert raw["status"] == "completed"
        assert raw.get("next_step") is None
        assert (tmp_path / "wt" / "spec" / "changes" / "orc-7" / "discovery.md").is_file()


def test_main_exit_zero_on_halt_complete():
    """halt_complete must not use process exit 1 (shell || would swallow JSON)."""
    from orchestrator_next.archive_completion import main

    repo = Path(__file__).resolve().parents[2]
    arch = repo / "spec" / "changes" / "archive" / "2026-05-25-orc-7"
    if not (arch / "state.yaml").is_file():
        pytest.skip("orc-7 archive fixture not present")
    code = main(["probe", str(repo), "orc-7", "ORC-7"])
    assert code == 0


def test_real_orc7_archive_fixture():
    """Integration: orchestrator repo archive for orc-7 is discoverable."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "spec" / "changes" / "archive" / "2026-05-25-orc-7" / "state.yaml").is_file():
        pytest.skip("orc-7 archive fixture not present")
    found = find_completed_archive(repo, slug="orc-7", ticket_id="ORC-7")
    assert found is not None
    assert found["archive_path"].endswith("2026-05-25-orc-7/")
