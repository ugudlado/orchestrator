"""run_cmd state resolution: resume existing state, don't seed duplicates;
`complete` resolves existing/archived state, never seeds fresh.

Regression for the port gap: the in-process run_cmd unconditionally seeded,
which broke resume (duplicate state files) and `orchestrator complete`
(seeded a fresh complete state instead of resuming the feature/archived state).
"""
from __future__ import annotations

import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from orchestrator_next import run_loop  # noqa: E402


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)
    (repo / "README.md").write_text("repo\n")
    return repo


def test_resolve_active_resumes_not_seeds(tmp_path):
    repo = _repo(tmp_path)
    sd = repo / ".orchestrator" / "orc-x"
    sd.mkdir(parents=True)
    existing = sd / "20260101T000000_feature_state.yaml"
    existing.write_text("change_id: orc-x\n")

    # Resolves the existing file — no new state file created.
    got = run_loop._resolve_active_state("orc-x", "feature", str(repo))
    assert got == str(existing)
    assert len(list(sd.glob("*_feature_state.yaml"))) == 1, "must not create a duplicate"


def test_resolve_active_empty_when_none(tmp_path):
    repo = _repo(tmp_path)
    assert run_loop._resolve_active_state("orc-none", "feature", str(repo)) == ""


def test_resolve_archived_state_for_complete(tmp_path):
    repo = _repo(tmp_path)
    # archived under spec/changes/archive/<dated>-<slug>/state.yaml
    arch = repo / "spec" / "changes" / "archive" / "2026-06-05-orc-y"
    arch.mkdir(parents=True)
    (arch / "state.yaml").write_text("change_id: orc-y\n")

    got = run_loop._resolve_archived_state("orc-y", str(repo))
    assert got == str(arch / "state.yaml")


def test_resolve_archived_direct_path(tmp_path):
    repo = _repo(tmp_path)
    arch = repo / "spec" / "changes" / "archive" / "orc-z"
    arch.mkdir(parents=True)
    (arch / "state.yaml").write_text("change_id: orc-z\n")
    assert run_loop._resolve_archived_state("orc-z", str(repo)) == str(arch / "state.yaml")
