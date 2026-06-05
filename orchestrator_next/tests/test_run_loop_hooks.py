"""pre:/post: contract lifecycle hooks, executed by the loop.

Verifies: (1) a pre hook runs before the step and its side effect is observable;
(2) a post hook runs after; (3) a failing pre hook blocks the workflow (exit 2);
(4) a failing post hook does NOT fail the step.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from orchestrator_next import run_loop  # noqa: E402


def _script_contract(contracts: Path, step_id: str, *, pre=None, post=None) -> None:
    d = contracts / step_id
    d.mkdir(parents=True)
    body = {"id": step_id, "version": 2, "run": "script.sh", "outputs": []}
    if pre:
        body["pre"] = pre
    if post:
        body["post"] = post
    (d / "contract.yaml").write_text(yaml.safe_dump(body))
    s = d / "script.sh"
    s.write_text("#!/usr/bin/env bash\necho '{}'\n")
    s.chmod(0o755)


def _state(repo: Path, nodes) -> Path:
    sd = repo / ".orchestrator" / "h"
    sd.mkdir(parents=True)
    sy = sd / "20260101T000000_feature_state.yaml"
    sy.write_text(yaml.safe_dump({
        "change_id": "h", "schema": "feature", "version": 1, "status": "active",
        "phase": "main", "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": nodes}},
        "step_history": [],
    }))
    return sy


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)
    (repo / "spec" / "project.yaml").write_text(yaml.safe_dump({
        "version": 1, "project": {"name": "t", "repo": "t", "summary": "s"},
        "quality_bar": {"max_spawn_failures": 3}, "rules": [],
    }))
    return repo


def test_pre_and_post_hooks_run(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    marker = tmp_path / "marks"
    marker.mkdir()
    contracts = tmp_path / "c"
    _script_contract(
        contracts, "step-h",
        pre=[f"touch {marker}/pre.txt"],
        post=[f"touch {marker}/post.txt"],
    )
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    sy = _state(repo, [{"id": "step-h", "status": "pending"}])

    code = run_loop.run_loop(str(sy), "", repo_root=str(repo), agents_yaml="")
    assert code == 1
    assert (marker / "pre.txt").exists(), "pre hook did not run"
    assert (marker / "post.txt").exists(), "post hook did not run"


def test_failing_pre_hook_blocks(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h", pre=["exit 1"])
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    sy = _state(repo, [{"id": "step-h", "status": "pending"}])

    code = run_loop.run_loop(str(sy), "", repo_root=str(repo), agents_yaml="")
    assert code == 2, f"failing pre hook must block (exit 2), got {code}"
    # Step body must NOT have recorded completed.
    hist = yaml.safe_load(sy.read_text()).get("step_history") or []
    assert not any(e.get("status") == "completed" for e in hist)


def test_failing_post_hook_is_non_fatal(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contracts = tmp_path / "c"
    _script_contract(contracts, "step-h", post=["exit 1"])
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    sy = _state(repo, [{"id": "step-h", "status": "pending"}])

    code = run_loop.run_loop(str(sy), "", repo_root=str(repo), agents_yaml="")
    assert code == 1, f"failing post hook must NOT fail the workflow, got {code}"
    hist = yaml.safe_load(sy.read_text()).get("step_history") or []
    assert any(e.get("step_id") == "step-h" and e.get("status") == "completed" for e in hist)
