#!/usr/bin/env python3
"""
Reproduction script for Bug 1: repeat-loop exit failure when tasks.md is missing
from the worktree-relative path even though a complete copy exists in repo_root.

Root cause (pre-fix): _resolve_workflow_artifact_path returned
<worktree_path>/spec/changes/<id>/tasks.md whenever the worktree DIRECTORY
existed on disk — regardless of whether tasks.md was ever written there.
_check_all_tasks_completed then returned False (fail-closed) because the file
was missing at that path, causing dispatch to re-emit execute-next-task
indefinitely even after all tasks were complete.

Fix (T-2): When the worktree dir exists, also verify the candidate file exists
(candidate.is_file()) before returning it. If it's absent, fall through to
repo_root resolution.

Expected (after fix): When tasks.md is present in repo_root with all tasks
checked, _check_all_tasks_completed returns True even when the worktree dir
exists but has no tasks.md.

Run from any directory:
    python3 repro_bug1.py
"""
import sys
import os
import tempfile
import shutil

# Add config/scripts to path so orchestrator_next is importable
# Import from the worktree to verify the fixed code
_ORC_ROOT = "/Users/spidey/code/feature_worktrees/orc-58"
sys.path.insert(0, os.path.join(_ORC_ROOT, "config", "scripts"))

from orchestrator_next.record import _check_all_tasks_completed

# A tasks.md where all tasks are checked (no open - [ ] items)
ALL_DONE_TASKS_MD = """\
## Tasks

- [x] T-1: First task
- [x] T-2: Second task
- [x] T-3: Third task
"""

# A tasks.md where one task is still open
INCOMPLETE_TASKS_MD = """\
## Tasks

- [x] T-1: First task
- [ ] T-2: Second task (incomplete)
"""


def main():
    # Set up a temporary repo_root with tasks.md (all complete)
    repo_root = tempfile.mkdtemp(prefix="orc-bug1-repo-")
    change_id = "orc-test"
    spec_dir = os.path.join(repo_root, "spec", "changes", change_id)
    os.makedirs(spec_dir, exist_ok=True)
    with open(os.path.join(spec_dir, "tasks.md"), "w") as f:
        f.write(ALL_DONE_TASKS_MD)

    # Worktree dir EXISTS on disk but tasks.md was never written there
    worktree_dir = tempfile.mkdtemp(prefix="orc-bug1-worktree-")

    try:
        state_raw = {
            "change_id": change_id,
            "worktree_path": worktree_dir,
            "repo_root": repo_root,
        }

        result = _check_all_tasks_completed(state_raw)
        print("=== Bug 1: resolver fall-through when worktree tasks.md absent ===")
        print()
        print(f"Scenario: worktree dir EXISTS, tasks.md NOT in worktree, tasks.md in repo_root (all complete)")
        print(f"  _check_all_tasks_completed = {result}")
        print(f"  Expected: True  (resolver falls through to repo_root; all tasks checked)")
        print()
        if result is True:
            print("FIXED: _check_all_tasks_completed correctly reads tasks.md from repo_root")
            print("when worktree-relative copy is absent.")
        else:
            print("BUG 1 CONFIRMED: _check_all_tasks_completed returned False even though")
            print("repo_root/spec/changes/<id>/tasks.md exists with all tasks complete.")
            print("The resolver was stuck on the worktree-relative path that doesn't exist.")

        # Control: verify False is still returned when tasks are genuinely incomplete
        print()
        print("--- Control: tasks.md in repo_root with open items ---")
        with open(os.path.join(spec_dir, "tasks.md"), "w") as f:
            f.write(INCOMPLETE_TASKS_MD)

        result2 = _check_all_tasks_completed(state_raw)
        print(f"  _check_all_tasks_completed = {result2}  (expected: False — open task remains)")
        if result2 is False:
            print("  Correct: returns False when incomplete tasks exist.")
        else:
            print("  REGRESSION: returned True with open tasks!")

    finally:
        shutil.rmtree(repo_root, ignore_errors=True)
        shutil.rmtree(worktree_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
