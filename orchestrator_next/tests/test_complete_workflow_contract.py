"""T-7 / T-12: step contract presence after the ORC-79 teardown collapse.

The new terminal `complete-workflow` step needs a dispatchable contract.
`merge-to-main` and `remove-worktree` no longer dispatch in any schema — their
contracts are deleted. `archive-completed-change.yaml` is RETAINED: spike.yaml
still dispatches `archive-completed-change` as its terminal step.

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
    reason="install symlink points ORCHESTRATOR_HOME at a different tree (e.g. feature worktree)",
)
def test_repo_and_home_step_dirs_are_the_same_tree():
    """The dual-tree guarantee: the HOME step dir resolves to the repo one
    (install.sh symlink), so a single edit covers both trees."""
    assert os.path.realpath(_HOME_STEPS) == os.path.realpath(_REPO_STEPS), (
        f"HOME steps {os.path.realpath(_HOME_STEPS)} != repo steps "
        f"{os.path.realpath(_REPO_STEPS)} — dual-tree assumption broken"
    )


def test_complete_workflow_contract_present():
    path = os.path.join(_REPO_STEPS, "complete-workflow", "contract.yaml")
    assert os.path.isfile(path), f"missing step contract: {path}"


def test_complete_workflow_contract_shape():
    path = os.path.join(_REPO_STEPS, "complete-workflow", "contract.yaml")
    assert os.path.isfile(path), f"missing step contract: {path}"
    contract = yaml.safe_load(open(path).read())
    assert contract.get("id") == "complete-workflow", (
        f"contract id must be 'complete-workflow', got {contract.get('id')!r}"
    )
    assert contract.get("run") == "script.sh", (
        f"contract run must be 'script.sh' (directory-form), "
        f"got {contract.get('run')!r}"
    )
    # complete-workflow declares NO `outputs:`. It is a state-mutating inline
    # step pre-recorded as `completed` BEFORE its script runs (ORC-66
    # crash-avoidance), so its stdout completion_record is never threaded into
    # step_history's evidence.outputs — a declared output would be
    # unverifiable and would fail the optimistic empty pre-record's
    # _check_declared_outputs. This matches the archive-completed-change.yaml
    # pre-record precedent, which also declares no `outputs:`.
    assert contract.get("outputs") in (None, []), (
        f"complete-workflow.yaml must declare no outputs (pre-record contract), "
        f"got {contract.get('outputs')!r}"
    )


# --- T-12: merge/removal contracts deleted; archive contract retained ------

def test_obsolete_step_contracts_absent():
    """`merge-to-main.yaml` and `remove-worktree.yaml` no longer dispatch in
    any schema — their contracts must be deleted."""
    for name in ("merge-to-main.yaml", "remove-worktree.yaml"):
        path = os.path.join(_REPO_STEPS, name)
        assert not os.path.isfile(path), (
            f"obsolete step contract should be deleted: {path}"
        )


def test_archive_completed_change_contract_retained():
    """`archive-completed-change/contract.yaml` is RETAINED — spike.yaml still
    dispatches `archive-completed-change` as its terminal step."""
    path = os.path.join(_REPO_STEPS, "archive-completed-change", "contract.yaml")
    assert os.path.isfile(path), (
        f"archive-completed-change/contract.yaml must be retained for spike: {path}"
    )
