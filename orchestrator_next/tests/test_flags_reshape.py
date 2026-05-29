"""merge_to_main is gone entirely; autopilot ends at the boundary (ORC-108).

Autopilot's merge-to-default behavior used to be a behavioral flag flipped by the
`--autopilot` CLI set. ORC-108 removes the flag — and the user's decision is that
autopilot does NOT merge at all: it runs the loop, archives, and exits at the
boundary (same end state as feature/bugfix), leaving the worktree/branch intact.
Merging is a deliberate, separate action (`orchestrator complete` / `/approve-qa`),
which merges unconditionally — invoking the verb IS the signal.

These tests prove the mechanism is fully removed:
  - `merge_to_main` is absent from the flag registry (gates AND behavioral)
  - `--autopilot` does not set it
  - NO workflow file declares `merge_to_main` (autopilot included)
  - the `worktree` flag stays removed (worktree is unconditional)

Dual-tree note: `~/.config/orchestrator/config/workflow.yaml` resolves (via the
install.sh config symlink) to the same physical file as the repo
`config/workflow.yaml` — one edit covers both trees.
"""
from __future__ import annotations

import os

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# ORC-105: flags merged into config/workflow.yaml (gates/behavioral/cli unchanged).
_REPO_FLAGS = os.path.join(_REPO_ROOT, "config", "workflow.yaml")
_HOME_FLAGS = os.path.expanduser("~/.config/orchestrator/config/workflow.yaml")
_WORKFLOWS_DIR = os.path.join(_REPO_ROOT, "config", "workflows")


def _load_flags():
    return yaml.safe_load(open(_REPO_FLAGS).read())


def _load_workflow(name):
    return yaml.safe_load(open(os.path.join(_WORKFLOWS_DIR, f"{name}.yaml")).read()) or {}


@pytest.mark.skipif(
    os.path.realpath(_HOME_FLAGS) != os.path.realpath(_REPO_FLAGS),
    reason="install symlink points ORCHESTRATOR_HOME at a different tree (e.g. feature worktree)",
)
def test_repo_and_home_flags_are_the_same_file():
    """Dual-tree guarantee: HOME flags.yaml resolves to the repo one."""
    assert os.path.realpath(_HOME_FLAGS) == os.path.realpath(_REPO_FLAGS), (
        f"HOME flags {os.path.realpath(_HOME_FLAGS)} != repo flags "
        f"{os.path.realpath(_REPO_FLAGS)} — dual-tree assumption broken"
    )


def test_merge_to_main_flag_removed():
    """ORC-108: merge_to_main is no longer a flag in any registry section."""
    flags = _load_flags()
    assert "merge_to_main" not in (flags.get("behavioral") or {}), (
        "merge_to_main must be removed from behavioral: — it is a workflow property now"
    )
    assert "merge_to_main" not in (flags.get("gates") or {}), (
        "merge_to_main must not be under gates:"
    )


def test_autopilot_flag_no_longer_sets_merge_to_main():
    """The --autopilot CLI set must not flip merge_to_main — it's a workflow property."""
    flags = _load_flags()
    autopilot = (flags.get("cli") or {}).get("--autopilot") or {}
    sets = autopilot.get("sets") or {}
    assert "merge_to_main" not in sets, (
        f"--autopilot must not set merge_to_main (it's a workflow property), got {sets!r}"
    )


@pytest.mark.parametrize("name", ["autopilot", "feature", "bugfix"])
def test_no_workflow_declares_merge_to_main(name):
    """No workflow auto-merges — they all end at the boundary. Autopilot included:
    it loops, archives, and exits; merge is the separate `orchestrator complete`."""
    wf = _load_workflow(name)
    assert not wf.get("merge_to_main", False), (
        f"{name}.yaml must not declare merge_to_main (no workflow auto-merges)"
    )


def test_worktree_flag_removed():
    """ORC-108: worktree is unconditional — the flag must not exist anywhere."""
    flags = _load_flags()
    assert "worktree" not in (flags.get("behavioral") or {}), "worktree flag must be removed"
    assert "worktree" not in (flags.get("gates") or {}), "worktree flag must be removed"
