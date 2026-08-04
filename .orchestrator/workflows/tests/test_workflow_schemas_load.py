"""
Workflow schema load test — exercises the real schemas in config/workflows/
through generate_plan to catch syntax breaks, missing step contracts,
and malformed flag definitions before they hit autopilot.

Closes the T-0 gap from .tmp/develop-schema-spec.md: previously no test
loaded the production workflow YAMLs, so freehand schema edits had no
automated safety net.

Each schema runs with its declared `defaults` flags. The workflow_plan
is derived directly from the schema's resolved phases, with every step
counted as active (gating-flag filtering is exercised separately by
test_generate_plan.test_light_flag_drops_filtered_steps).
"""

import os
import re
import sys
from pathlib import Path

import pytest
import yaml


_HERE = os.path.dirname(os.path.abspath(__file__))
# ORC-106: orchestrator_next package at repo root (orchestrator_next/tests -> repo root).
_REPO_ROOT_STR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT_STR not in sys.path:
    sys.path.insert(0, _REPO_ROOT_STR)

from orchestrator_next.generate_plan import generate_plan  # noqa: E402

_REPO_ROOT = Path(_REPO_ROOT_STR)
_REAL_HOME = _REPO_ROOT / "config"  # config/ holds workflows + steps
_WORKFLOWS_DIR = _REAL_HOME / "workflows"

# Schemas exercised by orchestrate / autopilot. ORC-108: autopilot is now a
# real steps-based workflow (config/workflows/autopilot.yaml), run via
# generate_plan like the others — no longer inline-script-driven.
# ORC-120: patch and design are first-class workflow schemas.
_USER_FACING_SCHEMAS = ["feature", "bugfix", "patch", "design", "autopilot"]

_STEP_REF_RE = re.compile(r"^([a-zA-Z0-9_-]+)(?:\s+if\s+(?:not\s+)?[a-zA-Z0-9_]+)?$")


def _step_id_of(entry):
    """Extract the step id from a schema step entry (string / skill / prompt / id)."""
    from orchestrator_next.workflow_steps import step_id_of

    return step_id_of(entry)


def _resolve_phases_for_test(schema):
    """Mirror generate_plan._resolve_phases minimally for legacy multi-phase schemas."""
    raw_phases = schema.get("phases", [])
    out = []
    for phase in raw_phases:
        out.append(phase)
    return out


def _build_workflow_plan(schema):
    """Build a workflow_plan that marks every declared step active.

    Phase-less schemas (top-level `steps:`) synthesize a single `main` phase
    matching the engine's _resolve_phases behavior.
    """
    if not schema.get("phases") and schema.get("steps"):
        active = []
        for step_entry in schema.get("steps", []) or []:
            step_id = _step_id_of(step_entry)
            if step_id and not step_id.startswith("_"):
                active.append(step_id)
        return {"main": {"active": active, "filtered": []}}

    plan = {}
    for phase in _resolve_phases_for_test(schema):
        name = phase.get("name")
        if not name:
            continue
        active = []
        for step_entry in phase.get("steps", []) or []:
            step_id = _step_id_of(step_entry)
            if step_id and not step_id.startswith("_"):
                active.append(step_id)
        plan[name] = {"active": active, "filtered": []}
    return plan


def _write_stub_project(repo_root: Path) -> None:
    """Minimal project.yaml — generate_plan only reads `rules` and `verify_commands`."""
    project = {
        "version": 1,
        "project": {"name": "schema-load-test", "repo": "schema-load-test"},
        "rules": [],
        "verify_commands": {"test": "pytest"},
    }
    p = repo_root / "spec" / "project.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(project, sort_keys=False))


def _write_state(state_dir: Path, schema_name: str, schema: dict) -> Path:
    workflow_plan = _build_workflow_plan(schema)
    first_phase = next(iter(workflow_plan)) if workflow_plan else ""
    state = {
        "change_id": f"schema-load-{schema_name}",
        "slug": f"schema-load-{schema_name}",
        "schema": schema_name,
        "status": "active",
        "repo_root": str(state_dir.parent.parent),
        "workflow_plan": workflow_plan,
        "phase": first_phase,
        "step_history": [],
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return p


@pytest.mark.parametrize("schema_name", _USER_FACING_SCHEMAS)
def test_real_schema_generates_plan(tmp_path, monkeypatch, schema_name):
    """Each production schema must promote state.yaml to the nodes shape,
    covering every active step (ORC-63: plan.yaml eliminated)."""
    schema_path = _WORKFLOWS_DIR / f"{schema_name}.yaml"
    assert schema_path.exists(), f"missing real schema at {schema_path}"
    schema = yaml.safe_load(schema_path.read_text())

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_stub_project(repo_root)
    state_path = _write_state(repo_root / ".state" / schema_name, schema_name, schema)

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(_REAL_HOME))

    generate_plan(str(state_path))

    # ORC-63: workflow_plan is promoted in place; no plan.yaml is produced.
    assert not (state_path.parent / "plan.yaml").exists(), (
        f"plan.yaml should not be written for {schema_name}"
    )
    state = yaml.safe_load(state_path.read_text())
    workflow_plan = state["workflow_plan"]

    expected_plan = _build_workflow_plan(schema)
    expected_phase_names = list(expected_plan.keys())
    actual_phase_names = list(workflow_plan.keys())
    assert actual_phase_names == expected_phase_names, (
        f"{schema_name}: phase order mismatch — expected {expected_phase_names}, got {actual_phase_names}"
    )

    for phase_name, phase_block in workflow_plan.items():
        expected_step_ids = expected_plan[phase_name]["active"]
        nodes = phase_block["nodes"]
        actual_step_ids = [n["id"] for n in nodes]
        assert actual_step_ids == expected_step_ids, (
            f"{schema_name}/{phase_name}: step list mismatch — "
            f"expected {expected_step_ids}, got {actual_step_ids}"
        )
        for node in nodes:
            step_id = node["id"]
            # Accept either the directory form (<id>/contract.yaml) or the legacy
            # flat form (<id>.yaml); select-workflow.yaml stays as a flat file.
            contract_path = _REAL_HOME / "steps" / step_id / "contract.yaml"
            flat_path = _REAL_HOME / "steps" / f"{step_id}.yaml"
            assert contract_path.exists() or flat_path.exists(), (
                f"{schema_name}/{phase_name}: step '{step_id}' has no contract at "
                f"{contract_path} or {flat_path} "
                f"(phantom reference — generate_plan silently skips these)"
            )
            assert node.get("status") == "pending", (
                f"{schema_name}/{phase_name}/{step_id}: node status must be 'pending' at init"
            )


# ---------------------------------------------------------------------------
# Terminal steps — develop schemas end at learn; complete/autopilot
# own their tails.
# ---------------------------------------------------------------------------


def _schema_step_ids(schema_name):
    schema = yaml.safe_load((_WORKFLOWS_DIR / f"{schema_name}.yaml").read_text())
    return [
        _step_id_of(e)
        for e in (schema.get("steps") or [])
        if _step_id_of(e)
    ]


def _schema_step_entry(schema_name, step_id):
    schema = yaml.safe_load((_WORKFLOWS_DIR / f"{schema_name}.yaml").read_text())
    for entry in schema.get("steps") or []:
        if _step_id_of(entry) == step_id:
            return entry
    return None


_SCHEMA_TERMINAL_STEP = {
    "feature": "workflow-report",
    "bugfix": "workflow-report",
    "patch": "workflow-report",
    "design": "workflow-report",
    "implement": "workflow-report",
    "autopilot": "workflow-report",
    "complete": "workflow-report",
}


@pytest.mark.parametrize("schema_name,terminal_step", list(_SCHEMA_TERMINAL_STEP.items()))
def test_schema_ends_at_expected_terminal(schema_name, terminal_step):
    """Each production schema ends at its workflow boundary step."""
    steps = _schema_step_ids(schema_name)
    assert steps and steps[-1] == terminal_step, (
        f"{schema_name}.yaml steps must end with {terminal_step!r}, "
        f"got tail {steps[-3:]}"
    )


def test_complete_schema_includes_ticket_done():
    """Complete workflow syncs the ticket to Done before the terminal report."""
    steps = _schema_step_ids("complete")
    assert "ticket-done" in steps
    assert steps.index("archive-completed-change") < steps.index("ticket-done")


def test_complete_schema_merge_teardown_order():
    """complete.yaml: archive → merge-to-main → remove-worktree → ticket-done → workflow-report."""
    steps = _schema_step_ids("complete")
    order = ["archive-completed-change", "merge-to-main", "remove-worktree", "ticket-done", "workflow-report"]
    indices = [steps.index(s) for s in order]
    assert indices == sorted(indices), (
        f"complete.yaml steps out of order: {list(zip(order, indices))}"
    )


def test_patch_schema_retry_edges():
    """patch.yaml: implement and review carry ORC-120 retry routing.

    Default-edged fields are omitted from the workflow entry:
      - max_retries defaults to 3 in record._resolve_routing
      - on_success defaults to advance (next declaration-order step)
    Only non-default routing survives in the schema.
    """
    implement = _schema_step_entry("patch", "implement")
    review = _schema_step_entry("patch", "code-review")
    assert isinstance(implement, dict)
    assert implement.get("on_failure") == "implement"
    assert "max_retries" not in implement  # engine default (3)
    assert isinstance(review, dict)
    assert "on_success" not in review  # advance to next step (ticket-qa)
    assert review.get("on_failure") == "implement"
    assert review.get("max_retries") == 8  # non-default, retained


def test_patch_schema_has_light_design_only():
    """patch.yaml carries a light design step but skips the heavy design phase.

    patch is the lightweight path: it keeps `design`
    (added in ec0c2a5) but skips the heavy steps — explore, diagnose,
    design-review, ux-design.
    """
    steps = _schema_step_ids("patch")
    heavy_design_steps = {"explore", "diagnose", "design-review", "ux-design"}
    assert heavy_design_steps.isdisjoint(set(steps)), (
        f"patch.yaml must skip the heavy design phase; found {heavy_design_steps & set(steps)}"
    )
    assert "design" in steps
    assert "implement" in steps
    assert steps.index("create-worktree") < steps.index("implement")
