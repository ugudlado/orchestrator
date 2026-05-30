"""Tests for `complete-workflow.sh` — archive only.

Merge and worktree removal run from `orchestrator complete` after the phase
succeeds (merge before teardown). These tests prove:
  - script does not invoke merge-to-main or remove-worktree
  - archive runs on the feature worktree when worktree=true
  - merge/worktree records are deferred in completion_record JSON
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_SCRIPT = os.path.join(_REPO_ROOT, "config", "steps", "complete-workflow", "script.sh")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def _build_repo_with_worktree(tmp_path, *, worktree,
                              worktree_exists=True, branch_unmerged=False):
    """Build a temp git repo + a feature worktree + a state.yaml fixture.

    Returns (state_yaml_path, repo_root, worktree_path, archive_path, branch).
    The state.yaml lives inside the worktree at
    <worktree>/spec/changes/<change_id>/state.yaml — the production layout.
    """
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

    # Make the feature branch differ from main so merge has work to do.
    fc = worktree_path / "feature.txt"
    fc.write_text("feature change\n")
    _git(worktree_path, "add", "-A")
    _git(worktree_path, "commit", "-q", "-m", "feature work")

    if branch_unmerged:
        # Add a divergent commit on main so `git branch -d` would refuse:
        # the feature branch is not an ancestor of main.
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
        # ORC-108: this script archives only — it never merges, so no merge
        # property is involved.
        "flags": {},
        "workflow_plan": {
            "main": {
                "nodes": [{"id": "complete-workflow", "status": "pending"}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_path = change_dir / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    (change_dir / "tasks.md").write_text("- [x] T-1 done\n")

    if not worktree_exists:
        # Remove the worktree dir from disk but keep the registration so the
        # state.yaml still points at a now-absent path (UC-E1).
        _git(repo, "worktree", "remove", "--force", str(worktree_path))

    return str(state_path), str(repo), str(worktree_path), archive_path, branch


def _run_script(state_path, repo_root):
    """Run complete-workflow.sh with the production inline-script env."""
    env = {
        **os.environ,
        "STATE_YAML_PATH": state_path,
        "REPO_ROOT": repo_root,
        "ORCHESTRATOR_HOME": _REPO_ROOT,
    }
    return subprocess.run(
        ["bash", _SCRIPT, ],
        cwd=os.path.dirname(state_path),
        capture_output=True, text=True, env=env,
    )


def _parse_completion(stdout):
    """Extract the {completion_record: ...} JSON object from script stdout."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "completion_record" in obj:
            return obj["completion_record"]
    raise AssertionError(f"no completion_record JSON in stdout:\n{stdout}")


# ---------------------------------------------------------------------------
# AC-3: body ordering
# ---------------------------------------------------------------------------

def test_body_state_reads_precede_archive_and_no_remove_worktree():
    """All state-read statements precede the archive invocation; worktree removal
    is not invoked from this script."""
    with open(_SCRIPT) as f:
        body = f.read()
    raw_lines = body.splitlines()
    lines = ["" if ln.lstrip().startswith("#") else ln for ln in raw_lines]

    def first_line(pat):
        rx = re.compile(pat)
        for i, ln in enumerate(lines):
            if rx.search(ln):
                return i
        return None

    archive_line = first_line(r'bash\b.*(?:archive-completed-change|_ARCHIVE_SCRIPT)')
    assert archive_line is not None, "no archive-completed-change script invocation"

    read_lines = [i for i, ln in enumerate(lines) if "read_state_env" in ln]
    assert read_lines, "no read_state_env call in complete-workflow.sh"
    assert max(read_lines) < archive_line, (
        "a read_state_env call appears at or after the archive invocation "
        f"(reads at {read_lines}, archive at {archive_line})"
    )
    assert "remove-worktree.sh" not in body, (
        "complete-workflow must not invoke remove-worktree; use complete-feature-teardown"
    )


def test_no_llm_tool_references():
    """The script must not name any specific LLM tool."""
    with open(_SCRIPT) as f:
        body = f.read().lower()
    for token in ("claude", "cursor", "codex", "copilot", "gpt-"):
        assert token not in body, f"LLM-tool reference {token!r} in script"


# ---------------------------------------------------------------------------
# Behavior: merge + archive + cleanup
# ---------------------------------------------------------------------------

def test_archive_on_worktree(tmp_path):
    """worktree=true → archive lands on feature worktree; merge/teardown deferred."""
    state, repo, wt, archive_path, _branch = _build_repo_with_worktree(
        tmp_path, worktree=True
    )
    result = _run_script(state, repo)
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    cr = _parse_completion(result.stdout)
    assert cr["merge_record"].get("skipped") is True
    assert cr["worktree_record"].get("skipped") is True

    archive_dir = os.path.join(wt, archive_path)
    assert os.path.isdir(archive_dir), f"archive dir missing: {archive_dir}"
    assert os.path.isfile(os.path.join(archive_dir, "state.yaml"))
    assert os.path.isfile(os.path.join(archive_dir, "tasks.md"))
    assert os.path.isdir(wt), "worktree kept for orchestrator complete merge/teardown"
