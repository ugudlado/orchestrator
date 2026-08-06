"""Exit-2 notification (ORCHESTRATOR_NOTIFY_CMD) on a blocked run.

State durability for headless runs is the RunStore now (see record.py's
_persist_if_materialized / test_run_store.py) — this file used to also
assert a git auto-commit of the (gitignored) state dir, which no longer
exists: state never lives in the repo, so there's nothing to commit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from orchestrator_next import run_loop  # noqa: E402

_GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _script_contract(contracts: Path, step_id: str) -> None:
    d = contracts / step_id
    d.mkdir(parents=True)
    body = {"id": step_id, "version": 2, "run": "script.sh", "outputs": []}
    (d / "contract.yaml").write_text(yaml.safe_dump(body))
    s = d / "script.sh"
    s.write_text("#!/usr/bin/env bash\necho '{}'\n")
    s.chmod(0o755)


def _state(repo: Path, nodes, step_history=None) -> Path:
    sd = repo / ".orchestrator" / "h"
    sd.mkdir(parents=True)
    sy = sd / "20260101T000000_feature_state.yaml"
    sy.write_text(yaml.safe_dump({
        "change_id": "h", "schema": "feature", "version": 1, "status": "active",
        "phase": "main", "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": nodes}},
        "step_history": step_history or [],
    }))
    return sy


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=_GIT_ENV)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)
    (repo / "README.md").write_text("repo\n")
    (repo / ".gitignore").write_text(".orchestrator/\n")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_blocked_run_notifies(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h")
    payload_file = tmp_path / "notify.json"
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_NOTIFY_CMD", f"cat > {payload_file}")
    # A blocked terminal entry makes dispatch exit 2 (signoff/halt path).
    sy = _state(repo, [{"id": "step-h", "status": "pending"}], step_history=[{
        "step_id": "step-h", "phase": "main", "status": "blocked",
        "agent": "developer", "attempt": 1,
        "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z",
    }])

    code = run_loop.run_loop(str(sy), repo_root=str(repo), models_yaml="")
    assert code == 2

    data = json.loads(payload_file.read_text())
    assert data["event"] == "blocked"
    assert data["change_id"] == "h"
    assert "blocked" in data["reason"]
    assert data["state_yaml_path"] == str(sy)


def test_no_notify_cmd_no_notification(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h")
    monkeypatch.delenv("ORCHESTRATOR_NOTIFY_CMD", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    sy = _state(repo, [{"id": "step-h", "status": "pending"}], step_history=[{
        "step_id": "step-h", "phase": "main", "status": "blocked",
        "agent": "developer", "attempt": 1,
        "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z",
    }])

    code = run_loop.run_loop(str(sy), repo_root=str(repo), models_yaml="")
    assert code == 2  # no exception even though no notify command is set
