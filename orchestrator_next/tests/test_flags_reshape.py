"""flags.yaml reshape — `merge_to_main` is a behavioral flag.

`merge_to_main` does not filter steps from a workflow's `steps:` list — it is
read by `complete-workflow.sh` to gate its merge phase. So it lives under the
`behavioral:` section, not `gates:`.

(ORC-108 removed the `worktree` flag entirely: worktree is unconditional.)

These tests prove:
  - `merge_to_main` is under `behavioral:`, not `gates:`
  - it carries no `steps:` key
  - its default stays False
  - `--autopilot` still resolves `merge_to_main: true`

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


def _load_flags():
    return yaml.safe_load(open(_REPO_FLAGS).read())


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


def test_merge_to_main_under_behavioral():
    flags = _load_flags()
    behavioral = flags.get("behavioral") or {}
    assert "merge_to_main" in behavioral, (
        "merge_to_main must be under behavioral:"
    )


def test_merge_to_main_absent_from_gates():
    flags = _load_flags()
    gates = flags.get("gates") or {}
    assert "merge_to_main" not in gates, (
        "merge_to_main must NOT be under gates: — it no longer filters steps"
    )


def test_worktree_flag_removed():
    """ORC-108: worktree is unconditional — the flag must not exist anywhere."""
    flags = _load_flags()
    assert "worktree" not in (flags.get("behavioral") or {}), "worktree flag must be removed"
    assert "worktree" not in (flags.get("gates") or {}), "worktree flag must be removed"


def test_merge_to_main_carries_no_steps_key():
    flags = _load_flags()
    behavioral = flags.get("behavioral") or {}
    entry = behavioral.get("merge_to_main") or {}
    assert "steps" not in entry, "behavioral flag merge_to_main must not carry a steps: key"


def test_defaults_preserved():
    flags = _load_flags()
    behavioral = flags.get("behavioral") or {}
    assert (behavioral.get("merge_to_main") or {}).get("default") is False, (
        "merge_to_main default must stay False"
    )


def test_autopilot_still_sets_merge_to_main():
    """The --autopilot CLI flag-set must still flip merge_to_main true."""
    flags = _load_flags()
    autopilot = (flags.get("cli") or {}).get("--autopilot") or {}
    sets = autopilot.get("sets") or {}
    assert sets.get("merge_to_main") is True, (
        f"--autopilot must still set merge_to_main: true, got {sets!r}"
    )
