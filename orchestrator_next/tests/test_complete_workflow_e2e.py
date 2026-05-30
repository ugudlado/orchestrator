"""T-17: end-to-end completion regression test for `archive-completed-change`.

Proves the full path through the real `bin/orchestrator`:
  - `orchestrator next` at terminal `archive-completed-change` archives on the feature
    worktree (merge/teardown deferred to `orchestrator complete`)
  - archive directory created with moved state.yaml / tasks.md
  - worktree kept after `next` (removed only by `orchestrator complete`)
  - a SECOND `orchestrator next` does NOT raise FileNotFoundError and does NOT
    exit 3 — the ORC-66 failure mode is structurally dissolved
  - no already-`completed` step id is re-dispatched

Second case: an unmerged branch — the archive step keeps the worktree and branch
(it never merges; merge is the separate `orchestrator complete` verb).
"""
from __future__ import annotations

import os
import subprocess
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def _run_next(state_path, metrics_db):
    env = {
        **os.environ,
        "ORCHESTRATOR_HOME": _REPO_ROOT,
        "METRICS_DB": metrics_db,
    }
    return subprocess.run(
        [sys.executable, _BIN, "next", state_path],
        capture_output=True, text=True, env=env,
    )


def _build(tmp_path, *, branch_unmerged=False):
    """Build a temp git repo + worktree + a state.yaml whose only pending node
    is the terminal `archive-completed-change` (all prior nodes `completed`).

    Returns (state_yaml_path, repo, worktree_path, archive_path, branch,
             metrics_db).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    change_id = "e2e-test"
    branch = f"feature/{change_id}"
    worktree_path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree_path))
    (worktree_path / "feature.txt").write_text("feature change\n")
    _git(worktree_path, "add", "-A")
    _git(worktree_path, "commit", "-q", "-m", "feature work")

    if branch_unmerged:
        (repo / "diverge.txt").write_text("diverge\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "diverge on main")

    change_dir = worktree_path / "spec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    archive_path = f"spec/changes/archive/2026-05-23-{change_id}"

    # All prior nodes completed; only archive-completed-change pending.
    state = {
        "change_id": change_id,
        "slug": change_id,
        "schema": "feature",
        "status": "active",
        "repo_root": str(repo),
        "worktree_path": str(worktree_path),
        "branch": branch,
        "archive_path": archive_path,
        # ORC-108: these tests drive `orchestrator next` (the archive-completed-change
        # archive step) only — they never reach the merge tail, so no merge
        # property is needed.
        "flags": {},
        "workflow_plan": {
            "main": {
                "nodes": [
                    {"id": "execute-next-task", "status": "completed",
                     "agent": "developer"},
                    {"id": "compute-swe-metrics", "status": "completed",
                     "agent": "inline"},
                    {"id": "archive-completed-change", "status": "pending",
                     "agent": "inline"},
                ],
                "filtered": [],
            }
        },
        "phase": "main",
        "next_step": {"phase": "main", "step_id": "archive-completed-change"},
        "step_history": [
            {"step_id": "execute-next-task", "phase": "main",
             "status": "completed", "agent": "developer", "attempt": 1},
            {"step_id": "compute-swe-metrics", "phase": "main",
             "status": "completed", "agent": "inline", "attempt": 1},
        ],
    }
    state_path = change_dir / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    (change_dir / "tasks.md").write_text("- [x] T-1 done\n")

    metrics_db = str(tmp_path / "metrics.duckdb")
    return (str(state_path), str(repo), str(worktree_path), archive_path,
            branch, metrics_db)


def _build_no_worktree(tmp_path):
    """Build a temp git repo with the state dir IN-PLACE at
    `$REPO_ROOT/spec/changes/$CHANGE_ID` — a `worktree=false` run. No worktree
    is created and `state.yaml` carries no `worktree_path`.

    Returns (state_yaml_path, repo, archive_path, metrics_db).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    change_id = "e2e-no-wt"
    change_dir = repo / "spec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    archive_path = f"spec/changes/archive/2026-05-23-{change_id}"

    state = {
        "change_id": change_id,
        "slug": change_id,
        "schema": "feature",
        "status": "active",
        "repo_root": str(repo),
        "archive_path": archive_path,
        # ORC-108: no merge_to_main key → no merge (feature ends at boundary).
        "flags": {},
        "workflow_plan": {
            "main": {
                "nodes": [
                    {"id": "execute-next-task", "status": "completed",
                     "agent": "developer"},
                    {"id": "compute-swe-metrics", "status": "completed",
                     "agent": "inline"},
                    {"id": "archive-completed-change", "status": "pending",
                     "agent": "inline"},
                ],
                "filtered": [],
            }
        },
        "phase": "main",
        "next_step": {"phase": "main", "step_id": "archive-completed-change"},
        "step_history": [
            {"step_id": "execute-next-task", "phase": "main",
             "status": "completed", "agent": "developer", "attempt": 1},
            {"step_id": "compute-swe-metrics", "phase": "main",
             "status": "completed", "agent": "inline", "attempt": 1},
        ],
    }
    state_path = change_dir / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    (change_dir / "tasks.md").write_text("- [x] T-1 done\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed change dir")

    metrics_db = str(tmp_path / "metrics.duckdb")
    return str(state_path), str(repo), archive_path, metrics_db


def test_e2e_complete_workflow_worktree_false(tmp_path):
    """ORC-80: a `worktree=false` run archives in-place. The state dir lives at
    `$REPO_ROOT/spec/changes/$CHANGE_ID`; archive-completed-change must move it to the
    archive (not skip with "WORKTREE_ROOT not set"), the cleanup phase skips on
    the worktree flag, and a second `next` exits 1 with no re-dispatch."""
    state_path, repo, archive_path, metrics_db = _build_no_worktree(tmp_path)
    source_dir = os.path.dirname(state_path)

    # --- first next: dispatches + runs archive-completed-change.sh ---
    r1 = _run_next(state_path, metrics_db)
    assert "FileNotFoundError" not in r1.stderr, (
        "first next raised FileNotFoundError:\n" + r1.stderr
    )
    assert "Traceback" not in r1.stderr, (
        "first next raised an unhandled exception:\n" + r1.stderr
    )
    assert r1.returncode == 0, (
        f"first next should exit 0 (inline step ran), got {r1.returncode}\n"
        f"stderr: {r1.stderr}"
    )

    # archive dir created with the moved state.yaml + tasks.md
    archive_dir = os.path.join(repo, archive_path)
    assert os.path.isdir(archive_dir), (
        f"archive dir missing: {archive_dir} — worktree=false run was not "
        f"archived (the ORC-80 bug: archive-completed-change.sh skipped)"
    )
    assert os.path.isfile(os.path.join(archive_dir, "state.yaml")), (
        "state.yaml not moved to archive"
    )
    assert os.path.isfile(os.path.join(archive_dir, "tasks.md")), (
        "tasks.md not moved to archive"
    )

    # the in-place source dir is gone (relocated, not copied)
    assert not os.path.isdir(source_dir), (
        f"in-place source dir still present after archive: {source_dir}"
    )

    # the source deletion is committed — no dangling unstaged removal
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert "spec/changes/e2e-no-wt" not in status, (
        f"in-place source deletion left uncommitted:\n{status}"
    )

    # --- second next: on the archived state.yaml — must exit 1, not 3 ---
    archived_state = os.path.join(archive_dir, "state.yaml")
    r2 = _run_next(archived_state, metrics_db)
    assert "FileNotFoundError" not in r2.stderr, (
        "second next raised FileNotFoundError:\n" + r2.stderr
    )
    assert r2.returncode == 1, (
        f"second next should exit 1 (workflow complete), got {r2.returncode}\n"
        f"stderr: {r2.stderr}"
    )

    # no already-completed step id is re-dispatched
    final_state = yaml.safe_load(open(archived_state).read())
    history = final_state.get("step_history") or []
    ent_counts = {
        sid: sum(1 for h in history if h.get("step_id") == sid)
        for sid in ("execute-next-task", "compute-swe-metrics",
                    "archive-completed-change")
    }
    assert all(c == 1 for c in ent_counts.values()), (
        f"a step id was re-dispatched — expected each exactly once, "
        f"got {ent_counts}"
    )


def test_e2e_complete_workflow_archive_keeps_worktree(tmp_path):
    """First `next` archives on worktree and keeps checkout; second `next` exits 1."""
    (state_path, repo, worktree_path, archive_path, branch,
     metrics_db) = _build(tmp_path)

    # --- first next: dispatches + runs archive-completed-change.sh ---
    r1 = _run_next(state_path, metrics_db)
    assert "FileNotFoundError" not in r1.stderr, (
        "first next raised FileNotFoundError:\n" + r1.stderr
    )
    assert "Traceback" not in r1.stderr, (
        "first next raised an unhandled exception:\n" + r1.stderr
    )
    assert r1.returncode == 0, (
        f"first next should exit 0 (inline step ran), got {r1.returncode}\n"
        f"stderr: {r1.stderr}"
    )

    archive_dir = os.path.join(worktree_path, archive_path)
    assert os.path.isdir(archive_dir), f"archive dir missing: {archive_dir}"
    archived_state = os.path.join(archive_dir, "state.yaml")
    assert os.path.isfile(archived_state), "state.yaml not moved to archive"
    assert os.path.isfile(os.path.join(archive_dir, "tasks.md")), (
        "tasks.md not moved to archive"
    )

    assert os.path.isdir(worktree_path), (
        "worktree remains until complete-feature-teardown"
    )

    # --- second next: on the archived state.yaml — must NOT exit 3 ---
    r2 = _run_next(archived_state, metrics_db)
    assert "FileNotFoundError" not in r2.stderr, (
        "second next raised FileNotFoundError — the ORC-66 bug:\n" + r2.stderr
    )
    assert "Traceback" not in r2.stderr, (
        "second next raised an unhandled exception:\n" + r2.stderr
    )
    assert r2.returncode == 1, (
        f"second next should exit 1 (workflow complete), got {r2.returncode} "
        f"— exit 3 would be the ORC-66 re-dispatch/FileNotFoundError failure\n"
        f"stderr: {r2.stderr}"
    )

    # no already-completed step id is re-dispatched: every step id appears
    # exactly once in step_history (the ORC-66 re-dispatch hazard would show
    # execute-next-task recorded twice).
    final_state = yaml.safe_load(open(archived_state).read())
    history = final_state.get("step_history") or []
    ent_counts = {
        sid: sum(1 for h in history if h.get("step_id") == sid)
        for sid in ("execute-next-task", "compute-swe-metrics",
                    "archive-completed-change")
    }
    assert all(c == 1 for c in ent_counts.values()), (
        f"a step id was re-dispatched — expected each exactly once, "
        f"got {ent_counts}. history: {[h.get('step_id') for h in history]}"
    )
    cw_entries = [h for h in history if h.get("step_id") == "archive-completed-change"]
    assert cw_entries[0].get("status") == "completed", (
        f"archive-completed-change entry not 'completed': {cw_entries[0]}"
    )


def test_e2e_archive_step_keeps_unmerged_branch(tmp_path):
    """The archive-completed-change archive step never merges: an unmerged feature
    branch → worktree kept, branch preserved, exit 0. (Merge is the separate
    `orchestrator complete` verb, not this step.)"""
    (state_path, repo, worktree_path, archive_path, branch,
     metrics_db) = _build(tmp_path, branch_unmerged=True)

    r1 = _run_next(state_path, metrics_db)
    assert "FileNotFoundError" not in r1.stderr, r1.stderr
    assert r1.returncode == 0, (
        f"next should exit 0, got {r1.returncode}\nstderr: {r1.stderr}"
    )

    assert os.path.isdir(worktree_path), "worktree not removed by archive-completed-change"

    branches = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert branch in branches, (
        f"unmerged branch {branch} was deleted; it must be preserved"
    )

    assert os.path.isdir(os.path.join(worktree_path, archive_path)), (
        "archive dir missing on worktree"
    )
