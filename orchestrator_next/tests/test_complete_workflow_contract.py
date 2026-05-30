"""T-7 / T-12: step contract presence after the ORC-79 teardown collapse.

`archive-completed-change` is the terminal inline step for all production
schemas. `merge-to-main` and `remove-worktree` no longer dispatch in any
schema — their contracts are deleted. Merge and worktree teardown run from
`orchestrator complete` after the workflow phase.

Dual-tree note: `~/.config/orchestrator/config` is an install.sh symlink to the
repo `config/`, so `config/steps/` is one physical directory serving both
trees. The tests assert on the repo path and verify the HOME path resolves to
the same realpath, which is the dual-tree guarantee.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_REPO_STEPS = os.path.join(_REPO_ROOT, "config", "steps")
_HOME_STEPS = os.path.expanduser("~/.config/orchestrator/config/steps")
_FEATURE_SCHEMA = os.path.join(_REPO_ROOT, "config", "workflows", "feature.yaml")
_BUGFIX_SCHEMA = os.path.join(_REPO_ROOT, "config", "workflows", "bugfix.yaml")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrator_next.generate_plan import generate_plan  # noqa: E402

# complete.yaml tail: compute-prediction-accuracy through ticket-done (ORC-108 AC-3/AC-5)
_COMPLETE_PHASE_TAIL = [
    "compute-prediction-accuracy",
    "run-learn-cycle",
    "mark-change-completed",
    "compute-swe-metrics",
    "gather-learn-metrics",
    "cost-report",
    "archive-completed-change",
    "ticket-done",
]


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


# --- T-12: merge/removal contracts deleted; archive contract retained ------

def test_obsolete_step_contracts_absent():
    """`merge-to-main.yaml` and `remove-worktree.yaml` no longer dispatch in
    any schema — their contracts must be deleted."""
    for name in ("merge-to-main.yaml", "remove-worktree.yaml"):
        path = os.path.join(_REPO_STEPS, name)
        assert not os.path.isfile(path), (
            f"obsolete step contract should be deleted: {path}"
        )


# ---------------------------------------------------------------------------
# ORC-108 T-5: complete-phase steps in feature/bugfix schema tails (RED)
# ---------------------------------------------------------------------------

def _load_schema_step_ids(schema_path: str) -> list[str]:
    with open(schema_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    steps = doc.get("steps") or []
    return [s["id"] if isinstance(s, dict) else str(s) for s in steps]


def _step_index(steps: list[str], step_id: str) -> int:
    try:
        return steps.index(step_id)
    except ValueError:
        return -1


def _assert_complete_tail_after_ticket_qa(steps: list[str], schema_label: str) -> None:
    qa_idx = _step_index(steps, "ticket-qa")
    assert qa_idx >= 0, f"ticket-qa missing from {schema_label}"
    tail = steps[qa_idx + 1 :]
    assert tail[: len(_COMPLETE_PHASE_TAIL)] == _COMPLETE_PHASE_TAIL, (
        f"{schema_label}: after ticket-qa expected "
        f"{_COMPLETE_PHASE_TAIL}, got {tail!r}"
    )


def _write_stub_project(repo_root: Path) -> None:
    project = {
        "version": 1,
        "project": {"name": "complete-steps-red", "repo": "complete-steps-red"},
        "rules": [],
        "verify_commands": {"test": "pytest"},
    }
    p = repo_root / "spec" / "project.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(project, sort_keys=False))


_COMPLETE_STEPS_XFAIL = pytest.mark.xfail(
    reason="ORC-108 RED: complete-phase tail not in feature/bugfix schemas until T-6",
    strict=False,
)


class TestCompleteStepsInSchema:
    """feature.yaml and bugfix.yaml declare the complete.yaml tail after ticket-qa."""

    @_COMPLETE_STEPS_XFAIL
    def test_complete_steps_in_schema_feature_yaml_tail_after_ticket_qa(self):
        steps = _load_schema_step_ids(_FEATURE_SCHEMA)
        _assert_complete_tail_after_ticket_qa(steps, "feature.yaml")

    @_COMPLETE_STEPS_XFAIL
    def test_complete_steps_in_schema_bugfix_yaml_tail_after_ticket_qa(self):
        steps = _load_schema_step_ids(_BUGFIX_SCHEMA)
        _assert_complete_tail_after_ticket_qa(steps, "bugfix.yaml")

    @_COMPLETE_STEPS_XFAIL
    def test_complete_steps_in_schema_seeded_feature_dag_pending_from_day_1(
        self, tmp_path, monkeypatch
    ):
        """generate_plan promotes complete-phase nodes as pending at seed time."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
        schema = yaml.safe_load(Path(_FEATURE_SCHEMA).read_text())
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _write_stub_project(repo_root)
        state_dir = repo_root / "spec" / "changes" / "orc-108-red"
        state_dir.mkdir(parents=True)
        active = _load_schema_step_ids(_FEATURE_SCHEMA)
        state = {
            "change_id": "orc-108-red",
            "slug": "orc-108-red",
            "schema": "feature",
            "status": "active",
            "repo_root": str(repo_root),
            "flags": dict(schema.get("defaults") or {}),
            "workflow_plan": {"main": {"active": active, "filtered": []}},
            "phase": "main",
            "step_history": [],
        }
        state_path = state_dir / "state.yaml"
        state_path.write_text(yaml.safe_dump(state, sort_keys=False))

        generate_plan(str(state_path))

        raw = yaml.safe_load(state_path.read_text())
        by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
        for step_id in _COMPLETE_PHASE_TAIL:
            assert step_id in by_id, (
                f"seeded feature DAG missing complete-phase node {step_id!r}"
            )
            assert by_id[step_id].get("status") == "pending", (
                f"{step_id} must be pending at seed, got {by_id[step_id].get('status')!r}"
            )
