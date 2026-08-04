"""Step-contract presence for the complete-phase teardown tail.

feature/bugfix pause at `ticket-qa`; `orchestrator complete` runs the finish
spine (complete.yaml). Its tail is archive-completed-change → merge-to-main →
remove-worktree → ticket-done, all of which dispatch as real directory-form
DAG steps (f0d65c4 made merge-to-main and remove-worktree explicit).

Dual-tree note: `~/.config/orchestrator/config` is an install.sh symlink to the
repo `config/`, so `config/steps/` is one physical directory serving both
trees. The tests assert on the repo path and verify the HOME path resolves to
the same realpath, which is the dual-tree guarantee.
"""
from __future__ import annotations

import os

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_REPO_STEPS = os.path.join(_REPO_ROOT, "config", "steps")
_HOME_STEPS = os.path.expanduser("~/.config/orchestrator/config/steps")


@pytest.mark.skipif(
    os.path.realpath(_HOME_STEPS) != os.path.realpath(_REPO_STEPS),
    reason="install symlink points ~/.config/orchestrator at a different tree (e.g. feature worktree)",
)
def test_repo_and_home_step_dirs_are_the_same_tree():
    """The dual-tree guarantee: the HOME step dir resolves to the repo one
    (install.sh symlink), so a single edit covers both trees."""
    assert os.path.realpath(_HOME_STEPS) == os.path.realpath(_REPO_STEPS), (
        f"HOME steps {os.path.realpath(_HOME_STEPS)} != repo steps "
        f"{os.path.realpath(_REPO_STEPS)} — dual-tree assumption broken"
    )


def test_archive_completed_change_contract_present():
    path = os.path.join(_REPO_STEPS, "archive-completed-change", "contract.yaml")
    assert os.path.isfile(path), f"missing step contract: {path}"


def test_archive_completed_change_contract_shape():
    path = os.path.join(_REPO_STEPS, "archive-completed-change", "contract.yaml")
    contract = yaml.safe_load(open(path).read())
    assert contract.get("id") == "archive-completed-change", (
        f"contract id must be 'archive-completed-change', got {contract.get('id')!r}"
    )
    assert contract.get("run") == "script.sh", (
        f"contract run must be 'script.sh' (directory-form), "
        f"got {contract.get('run')!r}"
    )
    assert contract.get("outputs") in (None, []), (
        f"archive-completed-change must declare no outputs (pre-record contract), "
        f"got {contract.get('outputs')!r}"
    )


def test_complete_workflow_contract_absent():
    path = os.path.join(_REPO_STEPS, "complete-workflow", "contract.yaml")
    assert not os.path.isfile(path), (
        f"complete-workflow wrapper step removed; archive is dispatched directly: {path}"
    )


# --- merge/teardown dispatch as directory-form DAG steps (f0d65c4) ---------

def test_merge_teardown_contracts_present():
    """merge-to-main and remove-worktree are real DAG steps in complete.yaml,
    so their directory-form contracts must exist (not flat-file, not absent)."""
    for name in ("merge-to-main", "remove-worktree"):
        path = os.path.join(_REPO_STEPS, name, "contract.yaml")
        assert os.path.isfile(path), f"missing DAG-step contract: {path}"
