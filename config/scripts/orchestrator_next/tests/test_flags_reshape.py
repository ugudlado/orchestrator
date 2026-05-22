"""T-14: flags.yaml reshape — `worktree` / `merge_to_main` become behavioral.

After ORC-79 collapses the teardown steps, `worktree` and `merge_to_main` no
longer filter steps from a workflow's `steps:` list — they are read directly
by `complete-workflow.sh` to gate its merge and cleanup phases. So they move
from the `gates:` section (step-filtering) to the `behavioral:` section.

These tests prove:
  - `worktree` and `merge_to_main` are under `behavioral:`, not `gates:`
  - neither carries a `steps:` key
  - the seed-state flag-default merge still yields both keys with their
    defaults (worktree=true, merge_to_main=false)
  - `--autopilot` still resolves `merge_to_main: true`

Dual-tree note: `~/.config/orchestrator/config/flags.yaml` resolves (via the
install.sh config symlink) to the same physical file as the repo
`config/flags.yaml` — one edit covers both trees.
"""
from __future__ import annotations

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_REPO_FLAGS = os.path.join(_REPO_ROOT, "config", "flags.yaml")
_HOME_FLAGS = os.path.expanduser("~/.config/orchestrator/config/flags.yaml")


def _load_flags():
    return yaml.safe_load(open(_REPO_FLAGS).read())


def test_repo_and_home_flags_are_the_same_file():
    """Dual-tree guarantee: HOME flags.yaml resolves to the repo one."""
    assert os.path.realpath(_HOME_FLAGS) == os.path.realpath(_REPO_FLAGS), (
        f"HOME flags {os.path.realpath(_HOME_FLAGS)} != repo flags "
        f"{os.path.realpath(_REPO_FLAGS)} — dual-tree assumption broken"
    )


def test_worktree_and_merge_to_main_under_behavioral():
    flags = _load_flags()
    behavioral = flags.get("behavioral") or {}
    assert "worktree" in behavioral, (
        "worktree must be under behavioral:"
    )
    assert "merge_to_main" in behavioral, (
        "merge_to_main must be under behavioral:"
    )


def test_worktree_and_merge_to_main_absent_from_gates():
    flags = _load_flags()
    gates = flags.get("gates") or {}
    assert "worktree" not in gates, (
        "worktree must NOT be under gates: — it no longer filters steps"
    )
    assert "merge_to_main" not in gates, (
        "merge_to_main must NOT be under gates: — it no longer filters steps"
    )


def test_neither_carries_a_steps_key():
    flags = _load_flags()
    behavioral = flags.get("behavioral") or {}
    for name in ("worktree", "merge_to_main"):
        entry = behavioral.get(name) or {}
        assert "steps" not in entry, (
            f"behavioral flag {name} must not carry a steps: key"
        )


def test_defaults_preserved():
    flags = _load_flags()
    behavioral = flags.get("behavioral") or {}
    assert (behavioral.get("worktree") or {}).get("default") is True, (
        "worktree default must stay True"
    )
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
