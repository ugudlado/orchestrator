"""Tests for `archive-completed-change` — move change dir to archive + commit.

Merge and worktree removal run from `orchestrator complete` after the phase
succeeds (merge before teardown). These tests prove:
  - script does not invoke merge-to-main or remove-worktree
  - archive runs on the feature worktree when worktree=true
"""
from __future__ import annotations

import json
import os
import subprocess

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )


def _build_repo_with_worktree(tmp_path, *, worktree,
                              worktree_exists=True, branch_unmerged=False):
    """Build a temp git repo + a feature worktree + a state.yaml fixture."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    change_id = "cw-test"
    branch = f"feature/{change_id}"
    worktree_path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree_path))

    fc = worktree_path / "feature.txt"
    fc.write_text("feature change\n")
    _git(worktree_path, "add", "-A")
    _git(worktree_path, "commit", "-q", "-m", "feature work")

    if branch_unmerged:
        (repo / "diverge.txt").write_text("diverge\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "diverge on main")

    change_dir = worktree_path / "spec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    archive_path = f"spec/changes/archive/2026-05-23-{change_id}"

    state = {
        "change_id": change_id,
        "slug": change_id,
        "schema": "feature",
        "status": "active",
        "repo_root": str(repo),
        "worktree_path": str(worktree_path),
        "branch": branch,
        "archive_path": archive_path,
        "workflow_plan": {
            "main": {
                "nodes": [{"id": "archive-completed-change", "status": "pending"}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_path = change_dir / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    (change_dir / "tasks.md").write_text("- [x] T-1 done\n")

    if not worktree_exists:
        _git(repo, "worktree", "remove", "--force", str(worktree_path))

    return str(state_path), str(repo), str(worktree_path), archive_path, branch


_SCRIPT = os.path.join(
    _REPO_ROOT, "config", "steps", "archive-completed-change", "script.sh"
)


def _run_script(state_path, repo_root, *, change_id, archive_path, worktree_root):
    env = {
        **os.environ,
        "REPO_ROOT": repo_root,
        "CHANGE_ID": change_id,
        "ARCHIVE_PATH": archive_path,
        "WORKTREE_ROOT": worktree_root,
        "STATE_YAML_PATH": state_path,
        "ORCHESTRATOR_STATE_YAML_PATH": state_path,
    }
    return subprocess.run(
        ["bash", _SCRIPT],
        cwd=os.path.dirname(state_path),
        capture_output=True,
        text=True,
        env=env,
    )


def _parse_archive(stdout):
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "archive_record" in obj:
            return obj["archive_record"]
    raise AssertionError(f"no archive_record JSON in stdout:\n{stdout}")


def test_script_uses_dispatch_env_not_read_state_env():
    with open(_SCRIPT) as f:
        body = f.read()
    assert "read_state_env" not in body
    assert "REPO_ROOT" in body
    assert "ARCHIVE_PATH" in body
    assert "remove-worktree" not in body


def test_no_llm_tool_references():
    with open(_SCRIPT) as f:
        body = f.read().lower()
    for token in ("claude", "cursor", "codex", "copilot", "gpt-"):
        assert token not in body, f"LLM-tool reference {token!r} in script"


def test_archive_on_worktree(tmp_path):
    """worktree=true → archive lands on feature worktree; merge/teardown deferred."""
    state, repo, wt, archive_path, _branch = _build_repo_with_worktree(
        tmp_path, worktree=True
    )
    result = _run_script(
        state, repo,
        change_id="cw-test",
        archive_path=archive_path,
        worktree_root=wt,
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    record = _parse_archive(result.stdout)
    assert record.get("skipped") is not True
    assert record.get("archive_path") == archive_path

    archive_dir = os.path.join(wt, archive_path)
    assert os.path.isdir(archive_dir), f"archive dir missing: {archive_dir}"
    assert os.path.isfile(os.path.join(archive_dir, "state.yaml"))
    assert os.path.isfile(os.path.join(archive_dir, "tasks.md"))
    assert os.path.isdir(wt), "worktree kept for orchestrator complete merge/teardown"
