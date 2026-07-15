"""Headless durability (auto-commit state) + exit-2 notification.

Verifies: (1) a blocked headless run commits the gitignored state dir,
pushes it to origin, and pipes a blocked event to ORCHESTRATOR_NOTIFY_CMD;
(2) a completing headless run auto-commits state at the record chokepoint;
(3) neither commit fires without the headless env vars.
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

# ponytail: GIT_DIR/GIT_INDEX_FILE/GIT_WORK_TREE leak in from an ambient git
# hook (e.g. pre-commit) and override -C, redirecting these calls at the
# real repo instead of the tmp fixture. Strip them so -C always wins.
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
    (repo / "spec" / "project.yaml").write_text(yaml.safe_dump({
        "version": 1, "project": {"name": "t", "repo": "t", "summary": "s"},
        "quality_bar": {"max_spawn_failures": 3}, "rules": [],
    }))
    (repo / ".gitignore").write_text(".orchestrator/\n")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], capture_output=True, env=_GIT_ENV)
    _git(repo, "remote", "add", "origin", str(origin))
    return repo


def _clear_headless_env(monkeypatch) -> None:
    for var in ("ORCHESTRATOR_HEADLESS", "CLAUDE_CODE_REMOTE", "ORCHESTRATOR_NOTIFY_CMD"):
        monkeypatch.delenv(var, raising=False)


def test_blocked_headless_commits_pushes_and_notifies(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h")
    payload_file = tmp_path / "notify.json"
    _clear_headless_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HEADLESS", "1")
    monkeypatch.setenv("ORCHESTRATOR_NOTIFY_CMD", f"cat > {payload_file}")
    # A blocked terminal entry makes dispatch exit 2 (signoff/halt path).
    sy = _state(repo, [{"id": "step-h", "status": "pending"}], step_history=[{
        "step_id": "step-h", "phase": "main", "status": "blocked",
        "agent": "developer", "attempt": 1,
        "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z",
    }])

    code = run_loop.run_loop(str(sy), repo_root=str(repo), models_yaml="")
    assert code == 2

    # State dir committed despite being gitignored.
    log = _git(repo, "log", "--oneline").stdout
    assert "orchestrator state for h" in log
    # Pushed to origin.
    remote_log = subprocess.run(
        ["git", "-C", str(tmp_path / "origin.git"), "log", "--oneline", "main"],
        capture_output=True, text=True, env=_GIT_ENV).stdout
    assert "orchestrator state for h" in remote_log
    # Notification payload delivered on stdin.
    data = json.loads(payload_file.read_text())
    assert data["event"] == "blocked"
    assert data["change_id"] == "h"
    assert "blocked" in data["reason"]
    assert data["state_yaml_path"] == str(sy)


def test_headless_run_commits_state_per_record(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h")
    _clear_headless_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HEADLESS", "1")
    sy = _state(repo, [{"id": "step-h", "status": "pending"}])

    code = run_loop.run_loop(str(sy), repo_root=str(repo), models_yaml="")
    assert code == 1

    log = _git(repo, "log", "--oneline").stdout
    assert "orchestrator state for h" in log
    # Everything under the state dir is committed — nothing left dangling.
    assert not _git(repo, "status", "--porcelain", "--", ".orchestrator").stdout.strip()


def test_not_headless_no_commits(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h")
    _clear_headless_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    sy = _state(repo, [{"id": "step-h", "status": "pending"}])

    code = run_loop.run_loop(str(sy), repo_root=str(repo), models_yaml="")
    assert code == 1
    assert "orchestrator state" not in _git(repo, "log", "--oneline").stdout
