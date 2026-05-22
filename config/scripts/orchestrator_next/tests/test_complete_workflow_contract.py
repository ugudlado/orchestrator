"""T-7: `complete-workflow.yaml` step contract presence (RED before T-8).

The new terminal `complete-workflow` step needs a dispatchable contract.

Dual-tree note: `~/.config/orchestrator/config` is an install.sh symlink to the
repo `config/`, so `config/steps/` is one physical directory serving both
trees. The tests assert on the repo path and verify the HOME path resolves to
the same realpath, which is the dual-tree guarantee.

T-12 extends this module with `test_obsolete_step_contracts_absent`.
"""
from __future__ import annotations

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_REPO_STEPS = os.path.join(_REPO_ROOT, "config", "steps")
_HOME_STEPS = os.path.expanduser("~/.config/orchestrator/config/steps")


def test_repo_and_home_step_dirs_are_the_same_tree():
    """The dual-tree guarantee: the HOME step dir resolves to the repo one
    (install.sh symlink), so a single edit covers both trees."""
    assert os.path.realpath(_HOME_STEPS) == os.path.realpath(_REPO_STEPS), (
        f"HOME steps {os.path.realpath(_HOME_STEPS)} != repo steps "
        f"{os.path.realpath(_REPO_STEPS)} — dual-tree assumption broken"
    )


def test_complete_workflow_contract_present():
    path = os.path.join(_REPO_STEPS, "complete-workflow.yaml")
    assert os.path.isfile(path), f"missing step contract: {path}"


def test_complete_workflow_contract_shape():
    path = os.path.join(_REPO_STEPS, "complete-workflow.yaml")
    assert os.path.isfile(path), f"missing step contract: {path}"
    contract = yaml.safe_load(open(path).read())
    assert contract.get("id") == "complete-workflow", (
        f"contract id must be 'complete-workflow', got {contract.get('id')!r}"
    )
    assert contract.get("run") == "scripts/inline/complete-workflow.sh", (
        f"contract run must point at complete-workflow.sh, "
        f"got {contract.get('run')!r}"
    )
    assert contract.get("outputs") == ["completion_record"], (
        f"contract outputs must be [completion_record], "
        f"got {contract.get('outputs')!r}"
    )
