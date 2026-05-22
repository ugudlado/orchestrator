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
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.generate_plan import generate_plan  # noqa: E402

_REAL_HOME = Path(_SCRIPTS_DIR)  # _SCRIPTS_DIR resolves to <repo>/config
_REPO_ROOT = _REAL_HOME.parent
_WORKFLOWS_DIR = _REAL_HOME / "workflows"

# Schemas exercised by orchestrate / autopilot. Excludes:
#   autopilot, bootstrap — inline-script-driven, not run via generate_plan
_USER_FACING_SCHEMAS = ["feature", "bugfix", "spike", "bootstrap"]

_STEP_REF_RE = re.compile(r"^([a-zA-Z0-9_-]+)(?:\s+if\s+(?:not\s+)?[a-zA-Z0-9_]+)?$")


def _step_id_of(entry):
    """Extract the step id from a schema step entry (string form or dict form)."""
    if isinstance(entry, str):
        m = _STEP_REF_RE.match(entry.strip())
        return m.group(1) if m else entry.strip()
    if isinstance(entry, dict):
        return entry.get("id") or entry.get("include")
    return None


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
        "flags": dict(schema.get("defaults") or {}),
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

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(_REPO_ROOT))

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
            contract_path = _REAL_HOME / "steps" / f"{step_id}.yaml"
            assert contract_path.exists(), (
                f"{schema_name}/{phase_name}: step '{step_id}' has no contract at {contract_path} "
                f"(phantom reference — generate_plan silently skips these)"
            )
            assert "agent" in node, (
                f"{schema_name}/{phase_name}/{step_id}: missing agent in resolved node"
            )
            assert node.get("status") == "pending", (
                f"{schema_name}/{phase_name}/{step_id}: node status must be 'pending' at init"
            )


# ---------------------------------------------------------------------------
# orc-79: the three teardown steps collapse into one terminal `complete-workflow`
# in feature/bugfix only. spike keeps `archive-completed-change` (architect
# ruling, 2026-05-23); bootstrap never had any of the three.
# ---------------------------------------------------------------------------

# Steps that leave the feature.yaml / bugfix.yaml tail under ORC-79. Note:
# `archive-completed-change` the step CONTRACT is retained for spike — only its
# appearance in the feature/bugfix tail is removed.
_FEATURE_BUGFIX_REMOVED_STEPS = {
    "archive-completed-change",
    "merge-to-main",
    "remove-worktree",
}


def _schema_step_ids(schema_name):
    schema = yaml.safe_load((_WORKFLOWS_DIR / f"{schema_name}.yaml").read_text())
    return [
        _step_id_of(e)
        for e in (schema.get("steps") or [])
        if _step_id_of(e)
    ]


@pytest.mark.parametrize("schema_name", ["feature", "bugfix"])
def test_schema_ends_with_complete_workflow(schema_name):
    """feature.yaml / bugfix.yaml steps must end with the single terminal
    `complete-workflow` step (orc-79)."""
    steps = _schema_step_ids(schema_name)
    assert steps and steps[-1] == "complete-workflow", (
        f"{schema_name}.yaml steps must end with 'complete-workflow', "
        f"got tail {steps[-3:]}"
    )


@pytest.mark.parametrize("schema_name", ["feature", "bugfix"])
def test_schema_drops_the_three_removed_steps(schema_name):
    """The three collapsed teardown steps must not appear in feature/bugfix."""
    steps = set(_schema_step_ids(schema_name))
    leftover = steps & _FEATURE_BUGFIX_REMOVED_STEPS
    assert not leftover, (
        f"{schema_name}.yaml still lists removed step(s) {leftover} — "
        f"these collapse into complete-workflow"
    )


def test_spike_schema_unchanged():
    """spike.yaml is left untouched by ORC-79 (architect ruling): it keeps
    `archive-completed-change` as its terminal step and never gains
    `complete-workflow`."""
    steps = _schema_step_ids("spike")
    assert steps and steps[-1] == "archive-completed-change", (
        f"spike.yaml must still end with 'archive-completed-change', "
        f"got tail {steps[-3:]}"
    )
    assert "complete-workflow" not in steps, (
        "spike.yaml must NOT gain complete-workflow — it is out of ORC-79 scope"
    )
    assert "merge-to-main" not in steps and "remove-worktree" not in steps, (
        "spike.yaml unexpectedly contains merge-to-main / remove-worktree"
    )


def test_bootstrap_schema_unchanged():
    """bootstrap.yaml never contained any teardown step and must not gain
    `complete-workflow`."""
    steps = set(_schema_step_ids("bootstrap"))
    assert not (steps & _FEATURE_BUGFIX_REMOVED_STEPS), (
        "bootstrap.yaml unexpectedly contains a teardown step"
    )
    assert "complete-workflow" not in steps, (
        "bootstrap.yaml unexpectedly gained complete-workflow"
    )
