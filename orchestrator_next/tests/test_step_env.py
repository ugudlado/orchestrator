"""Tests for orchestrator_next.step_env.inline_script_env."""

from __future__ import annotations


from orchestrator_next.parser import State
from orchestrator_next.step_env import inline_script_env


def _state(**raw_overrides) -> State:
    raw = {
        "change_id": "orc-env-test",
        "slug": "orc-env-test",
        "branch": "feat/orc-env-test",
        "worktree_path": "/tmp/wt",
        "archive_path": "spec/changes/archive/orc-env-test/",
        "repo_root": "/tmp/repo",
        **raw_overrides,
    }
    return State(
        change_id=str(raw.get("change_id") or ""),
        phase="complete",
        repo_root="/tmp/repo",
        workflow_dir="/tmp/wt",
        worktree_artifact_dir="/tmp/wt/spec/changes",
        raw=raw,
        step_history=[],
        workflow_plan={},
    )


def test_inline_script_env_sets_legacy_and_orchestrator_aliases():
    state = _state()
    env = inline_script_env(
        state,
        "/tmp/repo/spec/changes/orc-env-test/state.yaml",
        action_env={
            "ORCHESTRATOR_STEP_ID": "mark-change-completed",
            "ORCHESTRATOR_ATTEMPT": "2",
        },
    )
    assert env["STATE_YAML_PATH"] == env["ORCHESTRATOR_STATE_YAML_PATH"]
    assert env["REPO_ROOT"] == env["ORCHESTRATOR_REPO_ROOT"] == "/tmp/repo"
    assert env["CHANGE_ID"] == env["ORCHESTRATOR_CHANGE_ID"] == "orc-env-test"
    assert env["WORKTREE_ROOT"] == env["WORKTREE_PATH"] == "/tmp/wt"
    assert env["ORCHESTRATOR_WORKFLOW_DIR"] == "/tmp/wt"
    assert env["ARCHIVE_PATH"] == "spec/changes/archive/orc-env-test/"
    assert env["BRANCH"] == "feat/orc-env-test"
    assert env["ORCHESTRATOR_STEP_ID"] == "mark-change-completed"
    assert env["ORCHESTRATOR_ATTEMPT"] == "2"
    assert env["ORCHESTRATOR_HOME"]
    assert env["ORCHESTRATOR_SCRIPTS_DIR"].endswith("orchestrator_next/scripts")


def test_inline_script_env_action_env_overrides_attempt(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    repo = tmp_path / "orch"
    (repo / "config").mkdir(parents=True)
    state = State(
        change_id="x",
        phase="main",
        repo_root=str(repo),
        workflow_dir=str(repo),
        worktree_artifact_dir=str(repo),
        raw={"change_id": "x"},
        step_history=[],
        workflow_plan={},
    )
    env = inline_script_env(
        state,
        str(repo / "state.yaml"),
        action_env={"ORCHESTRATOR_ATTEMPT": "9"},
    )
    assert env["ORCHESTRATOR_ATTEMPT"] == "9"
