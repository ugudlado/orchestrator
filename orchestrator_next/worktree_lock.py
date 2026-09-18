"""Serialize the shared singletons inside one worktree, so parallel steps can share it.

The measured problem
--------------------
"Parallel steps touch different files, so one worktree is fine" is correct about
source files and irrelevant to what actually breaks. Two experiments, 8 workers,
8 perfectly disjoint files, one worktree:

    concurrent `git add <my-file> && git commit`   ->  1 of 8 commits landed
                                                       7x "Unable to create
                                                       .git/index.lock: File exists"

    concurrent read-modify-write of tasks.yaml     ->  1 of 8 status updates
                                                       survived; 7 lost updates

Neither failure involves two steps touching the same source file. Both are
**shared singletons**:

  * `.git/index` — one per worktree, and every `git add`/`git commit` takes an
    exclusive `.git/index.lock` for the duration. Git does not queue; it fails.
  * `tasks.yaml` — one per change, and `implement/SKILL.md` tells every step to
    read it, flip its own task to `completed`, and write the whole file back.
    That is a textbook lost update.

So the answer to "can parallel steps share a worktree" is **yes** — but you have
to serialize the singletons, not isolate the tree. Isolating the tree (a
worktree per step) also works and costs more: disk, a cold build cache per step,
and a real merge problem at the join.

What this module does
---------------------
`git_lock()` is an inter-process advisory lock scoped to one worktree, held only
for the length of a git plumbing call. Commits take milliseconds, so contention
is negligible next to a multi-minute agent turn.

It is a *file* lock (fcntl.flock), not a thread lock, deliberately: the workers
that need to be serialized are the agent subprocesses, which are separate
processes and may not even be children of the same run loop.

`tasks.yaml` is not solved here. It is solved by not letting agents write it
concurrently at all — see `record.apply_task_updates`, which moves the mutation
into the engine under the same compare-and-swap the state store uses. That
mirrors the `state_patch` channel scripts already use, and it makes the
code-review spot-audit stronger as a side effect: the engine now holds the
authoritative record of what each step claims to have completed.
"""
from __future__ import annotations

import errno
import fcntl
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_NAME = "orchestrator-worktree.lock"
DEFAULT_TIMEOUT_S = 120.0
ENV_DISABLE = "ORCHESTRATOR_DISABLE_WORKTREE_LOCK"


class WorktreeLockTimeout(TimeoutError):
    """Could not acquire the worktree lock in time."""


def lock_path(worktree: str | os.PathLike[str]) -> Path:
    """Where the lock file lives for a given worktree.

    Inside `.git/` when it is a directory, so the lock is invisible to
    `git status` and never accidentally committed. For a linked worktree
    `.git` is a *file* pointing at the real gitdir — fall back to a dotfile at
    the worktree root, which is gitignored by convention.
    """
    root = Path(worktree).resolve()
    git = root / ".git"
    if git.is_dir():
        return git / LOCK_NAME
    return root / f".{LOCK_NAME}"


@contextmanager
def git_lock(
    worktree: str | os.PathLike[str],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    label: str = "",
) -> Iterator[None]:
    """Hold the worktree's git lock for the body of the with-block.

    No-op when `run.disable_worktree_lock` is set — an escape hatch for
    anyone running strictly serially who does not want the extra file.
    """
    from orchestrator_next import settings
    if settings.get("run.disable_worktree_lock"):
        yield
        return

    path = lock_path(worktree)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Unwritable git dir: better to run unlocked than to fail the step.
        yield
        return

    deadline = time.monotonic() + timeout_s
    fh = open(path, "a+")
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise WorktreeLockTimeout(
                        f"could not acquire {path} within {timeout_s}s"
                        + (f" (for {label})" if label else "")
                    ) from exc
                time.sleep(0.01)
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()} {label}\n")
            fh.flush()
        except OSError:
            pass
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def git_in_worktree(
    worktree: str | os.PathLike[str],
    *args: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run one git command under the worktree lock.

    Every index-touching call an agent step makes — `add`, `commit`, `rm`,
    `stash` — must go through here (or be wrapped in `git_lock`) when
    `ORCHESTRATOR_MAX_PARALLEL > 1`. Read-only calls (`log`, `show`, `cat-file`)
    do not take the index lock and do not need it; `status` does write the index
    cache, so it is included in the conservative set below.
    """
    # Ambient GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE (e.g. leaked from a
    # pre-commit hook) override -C and would point this at the wrong repo —
    # the same guard record.autocommit_state already applies.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    with git_lock(worktree, timeout_s=timeout_s, label=" ".join(args[:2])):
        proc = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True, text=True, env=env,
        )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, proc.stdout, proc.stderr
        )
    return proc


#: git subcommands that take `.git/index.lock`. Anything here must be serialized.
INDEX_WRITING = frozenset({
    "add", "rm", "mv", "commit", "checkout", "switch", "restore", "reset",
    "stash", "merge", "rebase", "cherry-pick", "revert", "apply", "am",
    "status",  # writes the index cache extension
})


#: git global options that take a separate value argument, so the value must be
#: skipped too when hunting for the subcommand. `git -c core.x=1 add …` would
#: otherwise be classified on "core.x=1" and read as lock-free.
_VALUE_TAKING_GLOBALS = frozenset({"-c", "-C", "--git-dir", "--work-tree",
                                   "--namespace", "--exec-path"})


def needs_lock(argv: list[str]) -> bool:
    """True when this git argv would contend on the index lock."""
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_TAKING_GLOBALS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token in INDEX_WRITING
    return False
