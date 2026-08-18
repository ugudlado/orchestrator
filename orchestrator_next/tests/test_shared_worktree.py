"""Can parallel steps share one worktree? Yes — once the singletons are serialized.

Each pair below is the same scenario twice: unlocked (the failure, measured) and
locked (the fix). The unlocked cases are not decoration — they are the evidence
that the hazard is real and that it has nothing to do with two steps touching
the same source file. Every worker here writes its own file and stages only
that file.
"""
from __future__ import annotations

import concurrent.futures
import os
import random
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from orchestrator_next.record import apply_task_updates
from orchestrator_next.worktree_lock import (
    ENV_DISABLE,
    WorktreeLockTimeout,
    git_in_worktree,
    git_lock,
    lock_path,
    needs_lock,
)

WORKERS = 8


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "wt"
    (r / "src").mkdir(parents=True)
    for i in range(WORKERS):
        (r / "src" / f"f{i}.py").write_text("v0\n")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


# ------------------------------------------------------- hazard 1: git index
def test_unlocked_concurrent_commits_lose_almost_everything(repo, monkeypatch):
    """The premise under test: disjoint files, one worktree, no coordination."""
    monkeypatch.setenv(ENV_DISABLE, "1")

    def worker(i: int) -> bool:
        (repo / "src" / f"f{i}.py").write_text(f"task-{i}\n")
        _git(repo, "add", "--", f"src/f{i}.py")
        return _git(repo, "commit", "-q", "-m", f"feat: task-{i}",
                    "--", f"src/f{i}.py").returncode == 0

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(worker, range(WORKERS)))

    landed = _git(repo, "log", "--oneline").stdout.count("feat: task-")
    assert landed < WORKERS, (
        "expected .git/index.lock contention to drop commits; if this ever "
        "passes, git changed its locking and the fix below may be unnecessary"
    )
    assert sum(results) < WORKERS


def test_locked_concurrent_commits_all_land(repo):
    """Same disjoint-file workers, serialized only around the git calls."""
    def worker(i: int) -> bool:
        (repo / "src" / f"f{i}.py").write_text(f"task-{i}\n")
        with git_lock(repo, label=f"task-{i}"):
            _git(repo, "add", "--", f"src/f{i}.py")
            return _git(repo, "commit", "-q", "-m", f"feat: task-{i}",
                        "--", f"src/f{i}.py").returncode == 0

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(worker, range(WORKERS)))

    assert all(results), "every commit must land"
    landed = _git(repo, "log", "--oneline").stdout.count("feat: task-")
    assert landed == WORKERS
    assert _git(repo, "status", "--porcelain").stdout.strip() == "", "tree is clean"


def test_git_in_worktree_helper_serializes(repo):
    """The convenience wrapper does the same thing without a with-block."""
    def worker(i: int) -> int:
        (repo / "src" / f"f{i}.py").write_text(f"helper-{i}\n")
        git_in_worktree(repo, "add", "--", f"src/f{i}.py")
        return git_in_worktree(repo, "commit", "-q", "-m", f"fix: h{i}",
                               "--", f"src/f{i}.py").returncode

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        codes = list(ex.map(worker, range(WORKERS)))
    assert codes == [0] * WORKERS


def test_lock_is_cross_process_not_just_cross_thread(repo):
    """Agent steps are subprocesses, so a threading.Lock would not do."""
    script = repo / "grab.py"
    script.write_text(
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from orchestrator_next.worktree_lock import git_lock\n"
        "with git_lock(%r, label='child'):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(1.5)\n" % (str(Path(__file__).parents[2]), str(repo)),
        encoding="utf-8",
    )
    child = subprocess.Popen([os.sys.executable, str(script)],
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    t0 = time.monotonic()
    with git_lock(repo, timeout_s=10, label="parent"):
        waited = time.monotonic() - t0
    child.wait(timeout=10)
    assert waited > 0.8, f"parent should have waited for the child, waited {waited:.2f}s"


def test_lock_times_out_rather_than_hanging_forever(repo):
    with git_lock(repo, label="holder"):
        pass  # released
    fh_holder = git_lock(repo, label="outer")
    fh_holder.__enter__()
    try:
        script = repo / "block.py"
        script.write_text(
            "import sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from orchestrator_next.worktree_lock import git_lock\n"
            "with git_lock(%r, label='blocker'):\n"
            "    print('held', flush=True); time.sleep(3)\n"
            % (str(Path(__file__).parents[2]), str(repo)), encoding="utf-8")
    finally:
        fh_holder.__exit__(None, None, None)

    child = subprocess.Popen([os.sys.executable, str(repo / "block.py")],
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    with pytest.raises(WorktreeLockTimeout):
        with git_lock(repo, timeout_s=0.3, label="impatient"):
            pass
    child.kill()


def test_lock_file_lives_inside_dot_git_and_is_invisible_to_status(repo):
    with git_lock(repo, label="x"):
        pass
    assert lock_path(repo) == repo / ".git" / "orchestrator-worktree.lock"
    assert lock_path(repo).is_file()
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_needs_lock_classifies_git_subcommands():
    assert needs_lock(["add", "--", "x.py"])
    assert needs_lock(["commit", "-m", "x"])
    assert needs_lock(["-c", "core.hooks=", "add", "x"])   # skips flags
    assert needs_lock(["status", "--porcelain"])           # writes index cache
    assert not needs_lock(["log", "--oneline"])
    assert not needs_lock(["show", "HEAD"])
    assert not needs_lock(["rev-parse", "HEAD"])


def test_disable_env_makes_it_a_noop(repo, monkeypatch):
    monkeypatch.setenv(ENV_DISABLE, "1")
    with git_lock(repo, label="x"):
        pass
    assert not lock_path(repo).exists()


# ------------------------------------------------------ hazard 2: tasks.yaml
def _tasks_fixture(repo: Path, change_id: str = "orc-par") -> Path:
    d = repo / "spec" / "changes" / change_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "tasks.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "tasks": [
        {"id": f"T-{i}", "title": f"t{i}", "files": [f"src/f{i}.py"],
         "verify": ["true"], "status": "pending"} for i in range(WORKERS)
    ]}), encoding="utf-8")
    return p


def test_agents_writing_tasks_yaml_concurrently_lose_updates(repo):
    """What implement/SKILL.md currently prescribes, run in parallel.

    The interleaving is forced with a barrier rather than left to the
    scheduler: an unsynchronized version passes by luck often enough to be a
    flaky test, and a flaky test proving a race is worse than no test. Every
    worker reads before any worker writes — which is exactly the window a real
    parallel run opens while the model is thinking.
    """
    import threading

    p = _tasks_fixture(repo)
    read_done = threading.Barrier(WORKERS)

    def worker(i: int) -> None:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        read_done.wait(timeout=10)                       # all read the same doc
        for t in doc["tasks"]:
            if t["id"] == f"T-{i}":
                t["status"] = "completed"
        time.sleep(random.uniform(0.001, 0.01))
        p.write_text(yaml.safe_dump(doc), encoding="utf-8")

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(worker, range(WORKERS)))

    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    done = [t["id"] for t in doc["tasks"] if t.get("status") == "completed"]
    assert len(done) == 1, (
        f"last writer should win outright, leaving 1 of {WORKERS} updates; "
        f"got {len(done)}: {done}"
    )


def test_engine_applied_task_updates_all_survive(repo):
    """The fix: the agent reports, the engine applies — locked and re-read."""
    p = _tasks_fixture(repo)
    state_raw = {"change_id": "orc-par", "worktree_path": str(repo),
                 "repo_root": str(repo)}

    def worker(i: int) -> list:
        time.sleep(random.uniform(0.005, 0.03))
        payload = {"task_updates": [
            {"id": f"T-{i}", "status": "completed",
             "tokens_in": 1000 + i, "duration_s": 10 + i}
        ]}
        return apply_task_updates(payload, state_raw, worktree=str(repo))

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        applied = list(ex.map(worker, range(WORKERS)))

    assert all(len(a) == 1 for a in applied), "every update must be applied"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in doc["tasks"]}
    assert all(by_id[f"T-{i}"]["status"] == "completed" for i in range(WORKERS))
    assert all(by_id[f"T-{i}"]["tokens_in"] == 1000 + i for i in range(WORKERS)), \
        "per-task fields must not bleed between workers"


def test_task_updates_merge_rather_than_replace(repo):
    p = _tasks_fixture(repo)
    state_raw = {"change_id": "orc-par", "worktree_path": str(repo)}
    apply_task_updates({"task_updates": [{"id": "T-1", "status": "completed"}]},
                       state_raw, worktree=str(repo))
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    t1 = next(t for t in doc["tasks"] if t["id"] == "T-1")
    assert t1["status"] == "completed"
    assert t1["files"] == ["src/f1.py"], "existing fields must be preserved"
    assert t1["title"] == "t1"


def test_unknown_task_id_is_dropped_not_created(repo, capsys):
    """A hallucinated task id must not silently become a real task."""
    p = _tasks_fixture(repo)
    state_raw = {"change_id": "orc-par", "worktree_path": str(repo)}
    applied = apply_task_updates(
        {"task_updates": [{"id": "T-999", "status": "completed"}]},
        state_raw, worktree=str(repo))
    assert applied == []
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert len(doc["tasks"]) == WORKERS
    assert "unknown task id" in capsys.readouterr().err


def test_missing_tasks_yaml_is_not_fatal(repo):
    """Patch schema runs have no tasks.yaml at all."""
    state_raw = {"change_id": "nope", "worktree_path": str(repo)}
    assert apply_task_updates({"task_updates": [{"id": "T-1"}]},
                              state_raw, worktree=str(repo)) == []


def test_no_task_updates_is_a_cheap_noop(repo):
    state_raw = {"change_id": "orc-par", "worktree_path": str(repo)}
    assert apply_task_updates({}, state_raw, worktree=str(repo)) == []
    assert apply_task_updates({"task_updates": []}, state_raw, worktree=str(repo)) == []


# --------------------------------------------------------- the two together
def test_eight_parallel_steps_share_one_worktree_cleanly(repo):
    """The scenario end to end: disjoint files, shared tree, commit + task update."""
    p = _tasks_fixture(repo)
    state_raw = {"change_id": "orc-par", "worktree_path": str(repo)}

    def step(i: int) -> bool:
        (repo / "src" / f"f{i}.py").write_text(f"impl-{i}\n")
        with git_lock(repo, label=f"T-{i}"):
            _git(repo, "add", "--", f"src/f{i}.py")
            ok = _git(repo, "commit", "-q", "-m", f"feat(orc-par): T-{i}",
                      "--", f"src/f{i}.py").returncode == 0
        applied = apply_task_updates(
            {"task_updates": [{"id": f"T-{i}", "status": "completed"}]},
            state_raw, worktree=str(repo))
        return ok and len(applied) == 1

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        assert all(ex.map(step, range(WORKERS)))

    assert _git(repo, "log", "--oneline").stdout.count("feat(orc-par):") == WORKERS
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert all(t["status"] == "completed" for t in doc["tasks"])
    # spec/ is untracked by construction in this fixture; what matters is that
    # no TRACKED file was left dirty — i.e. every worker's edit got committed.
    dirty = [ln for ln in _git(repo, "status", "--porcelain").stdout.splitlines()
             if not ln.startswith("??")]
    assert dirty == [], dirty
