"""
Tests for orchestrator_next.generate_plan.

Six tests per design.md § Testing Strategy:
  test_light_flag_drops_filtered_steps
  test_rule_merge_precedence
  test_byte_stable_output
  test_repeat_until_preserved
  test_phase_verify_attached_to_last_step
  test_include_phase_resolved
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.generate_plan import generate_plan  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _write_step_contract(contracts_dir: Path, step_id: str, data: dict) -> None:
    (contracts_dir / f"{step_id}.yaml").write_text(yaml.safe_dump(data))


def _make_project_yaml(tmp_path: Path, rules: list) -> Path:
    project = {
        "version": 1,
        "project": {"name": "test-repo", "repo": "test-repo", "summary": "Test project"},
        "rules": rules,
        "verify_commands": {"test": "pytest"},
    }
    p = tmp_path / "spec" / "project.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(project, sort_keys=False))
    return p


def _make_state_yaml(
    tmp_path: Path,
    schema: str,
    flags: dict,
    workflow_plan: dict,
    repo_root: str | None = None,
) -> Path:
    state = {
        "change_id": "test-feature",
        "slug": "test-feature",
        "schema": schema,
        "status": "active",
        "repo_root": repo_root or str(tmp_path),
        "flags": flags,
        "workflow_plan": workflow_plan,
        "phase": list(workflow_plan.keys())[0],
        "step_history": [],
    }
    state_dir = tmp_path / ".state" / "test-feature"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return path


def _make_schema_yaml(workflows_dir: Path, name: str, schema: dict) -> None:
    (workflows_dir / f"{name}.yaml").write_text(yaml.safe_dump(schema, sort_keys=False))


# ---------------------------------------------------------------------------
# test_light_flag_drops_filtered_steps
# ---------------------------------------------------------------------------


def test_light_flag_drops_filtered_steps(tmp_path, monkeypatch):
    """Plan.yaml must only include active steps — no explore/ux-design in light mode."""
    # Arrange: schema with explore, ux-design gated, and others ungated
    schema = {
        "name": "feature",
        "version": 1,
        "rules": [],
        "phases": [
            {
                "name": "specify",
                "goal": "Produce spec artifacts.",
                "rules": [],
                "steps": [
                    "explore if discovery",
                    "ux-design if ux_design",
                    "design-and-draft-artifacts",
                ],
            }
        ],
    }
    flags = {
        "light": True,
        "discovery": False,
        "ux_design": False,
        "tdd_required": False,
    }
    # Only active steps (filtered ones already resolved out of workflow_plan)
    workflow_plan = {
        "specify": {
            "active": ["design-and-draft-artifacts"],
            "filtered": [
                {"id": "explore", "reason": "flag discovery=false"},
                {"id": "ux-design", "reason": "flag ux_design=false"},
            ],
        }
    }

    # Write supporting files
    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, "feature", schema)
    _write_step_contract(
        contracts_dir,
        "design-and-draft-artifacts",
        {"id": "design-and-draft-artifacts", "agent": "architect", "inputs": [], "outputs": [], "rules": []},
    )
    _make_project_yaml(tmp_path, [])

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_path = _make_state_yaml(tmp_path, "feature", flags, workflow_plan)

    # Act
    generate_plan(str(state_path))

    # Assert
    plan_path = state_path.parent / "plan.yaml"
    assert plan_path.exists()
    plan = yaml.safe_load(plan_path.read_text())

    specify_phase = next(p for p in plan["phases"] if p["name"] == "specify")
    step_ids = [s["id"] for s in specify_phase["steps"]]
    assert step_ids == ["design-and-draft-artifacts"], (
        f"Expected only active steps, got: {step_ids}"
    )
    assert "explore" not in step_ids
    assert "ux-design" not in step_ids


# ---------------------------------------------------------------------------
# test_rule_merge_precedence
# ---------------------------------------------------------------------------


def test_rule_merge_precedence(tmp_path, monkeypatch):
    """Step-entry injection > contract > phase > schema named > project named.

    Also validates named-rule deduplication: schema overrides project for same id.
    """
    schema = {
        "name": "test-schema",
        "version": 1,
        "rules": [
            {"id": "named-both", "rule": "Schema wins on named-both."},
            {"id": "schema-only", "rule": "Schema-only rule."},
        ],
        "phases": [
            {
                "name": "work",
                "goal": "Do work.",
                "rules": ["Phase-level rule."],
                "steps": [
                    {
                        "id": "my-step",
                        "extra_rules": ["Extra injection rule."],
                        "rules_when": {
                            "some_flag": ["Injected from rules_when."],
                        },
                    }
                ],
            }
        ],
    }
    flags = {"some_flag": True}
    workflow_plan = {
        "work": {
            "active": ["my-step"],
            "filtered": [],
        }
    }
    project_rules = [
        {"id": "named-both", "rule": "Project loses on named-both."},
        {"id": "project-only", "rule": "Project-only rule."},
    ]

    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, "test-schema", schema)
    _write_step_contract(
        contracts_dir,
        "my-step",
        {
            "id": "my-step",
            "agent": "developer",
            "inputs": [],
            "outputs": [],
            "rules": ["Contract-level rule."],
        },
    )
    _make_project_yaml(tmp_path, project_rules)

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_path = _make_state_yaml(tmp_path, "test-schema", flags, workflow_plan)

    # Act
    generate_plan(str(state_path))

    plan = yaml.safe_load((state_path.parent / "plan.yaml").read_text())
    step = plan["phases"][0]["steps"][0]
    rules = step["rules"]

    # Tier 1 (injected) must appear before tier 2 (contract)
    idx_injected_when = rules.index("Injected from rules_when.")
    idx_extra = rules.index("Extra injection rule.")
    idx_contract = rules.index("Contract-level rule.")
    idx_phase = rules.index("Phase-level rule.")

    # Named rules: only one entry for named-both (deduped), schema wins
    assert "Schema wins on named-both." in rules
    assert "Project loses on named-both." not in rules

    # Precedence order
    assert idx_injected_when < idx_contract, "rules_when injection must precede contract rules"
    assert idx_extra < idx_contract, "extra_rules injection must precede contract rules"
    assert idx_contract < idx_phase, "contract rules must precede phase rules"
    idx_schema_only = rules.index("Schema-only rule.")
    assert idx_phase < idx_schema_only, "phase rules must precede named rules"

    # Project-only named rule should be present (no filter condition)
    assert "Project-only rule." in rules


# ---------------------------------------------------------------------------
# test_byte_stable_output
# ---------------------------------------------------------------------------


def test_byte_stable_output(tmp_path, monkeypatch):
    """Running generate_plan twice on the same state.yaml produces identical bytes."""
    schema = {
        "name": "feature",
        "version": 1,
        "rules": [{"id": "r1", "rule": "A named rule."}],
        "phases": [
            {
                "name": "specify",
                "goal": "Specify things.",
                "rules": ["Phase rule."],
                "steps": ["step-a", "step-b"],
            }
        ],
    }
    flags = {}
    workflow_plan = {
        "specify": {"active": ["step-a", "step-b"], "filtered": []}
    }

    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, "feature", schema)
    for sid in ("step-a", "step-b"):
        _write_step_contract(
            contracts_dir, sid,
            {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": [f"Rule for {sid}."]},
        )
    _make_project_yaml(tmp_path, [])

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_path = _make_state_yaml(tmp_path, "feature", flags, workflow_plan)

    # Run twice
    generate_plan(str(state_path))
    first = (state_path.parent / "plan.yaml").read_bytes()

    generate_plan(str(state_path))
    second = (state_path.parent / "plan.yaml").read_bytes()

    assert first == second, "Two runs produced different bytes — output is not deterministic"


# ---------------------------------------------------------------------------
# test_repeat_until_preserved
# ---------------------------------------------------------------------------


def test_repeat_until_preserved(tmp_path, monkeypatch):
    """execute-next-task step block must carry repeat_until from schema step entry."""
    schema = {
        "name": "feature",
        "version": 1,
        "rules": [],
        "phases": [
            {
                "name": "implement",
                "goal": "Implement tasks.",
                "rules": [],
                "steps": [
                    {
                        "id": "execute-next-task",
                        "repeat_until": "all_tasks_completed",
                    }
                ],
            }
        ],
    }
    flags = {}
    workflow_plan = {
        "implement": {"active": ["execute-next-task"], "filtered": []}
    }

    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, "feature", schema)
    _write_step_contract(
        contracts_dir,
        "execute-next-task",
        {
            "id": "execute-next-task",
            "agent": "developer",
            "inputs": [],
            "outputs": ["task_execution_result"],
            "rules": [],
            "repeat_until": "all_tasks_completed",
        },
    )
    _make_project_yaml(tmp_path, [])

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_path = _make_state_yaml(tmp_path, "feature", flags, workflow_plan)

    generate_plan(str(state_path))
    plan = yaml.safe_load((state_path.parent / "plan.yaml").read_text())

    step = plan["phases"][0]["steps"][0]
    assert step["id"] == "execute-next-task"
    assert step.get("repeat_until") == "all_tasks_completed", (
        f"repeat_until not preserved on execute-next-task, got: {step.get('repeat_until')!r}"
    )


# ---------------------------------------------------------------------------
# test_phase_verify_attached_to_last_step
# ---------------------------------------------------------------------------


def test_phase_verify_attached_to_last_step(tmp_path, monkeypatch):
    """verify block from schema phase must appear only on the last active step."""
    schema = {
        "name": "feature",
        "version": 1,
        "rules": [],
        "phases": [
            {
                "name": "specify",
                "goal": "Specify.",
                "rules": [],
                "verify": {
                    "assertions": ["design.md exists"],
                },
                "steps": ["step-first", "step-last"],
            }
        ],
    }
    flags = {}
    workflow_plan = {
        "specify": {"active": ["step-first", "step-last"], "filtered": []}
    }

    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, "feature", schema)
    for sid in ("step-first", "step-last"):
        _write_step_contract(
            contracts_dir, sid,
            {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": []},
        )
    _make_project_yaml(tmp_path, [])

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_path = _make_state_yaml(tmp_path, "feature", flags, workflow_plan)

    generate_plan(str(state_path))
    plan = yaml.safe_load((state_path.parent / "plan.yaml").read_text())

    steps = plan["phases"][0]["steps"]
    assert len(steps) == 2

    # First step must NOT have verify
    assert "verify" not in steps[0], (
        f"First step should not have verify block, but got: {steps[0].get('verify')}"
    )
    # Last step MUST have verify
    assert "verify" in steps[-1], (
        f"Last step should have verify block, but it is absent"
    )
    assert steps[-1]["verify"]["assertions"] == ["design.md exists"]


# ---------------------------------------------------------------------------
# test_include_phase_resolved
# ---------------------------------------------------------------------------


def test_include_phase_resolved(tmp_path, monkeypatch):
    """include: _complete-phase must be expanded inline in the plan."""
    # Schema with an include directive
    schema = {
        "name": "feature",
        "version": 1,
        "rules": [],
        "phases": [
            {
                "name": "specify",
                "goal": "Specify.",
                "rules": [],
                "steps": ["step-a"],
            },
            {"include": "_complete-phase"},
        ],
    }
    complete_phase = {
        "name": "complete",
        "goal": "Archive and complete.",
        "rules": ["Archive only from canonical active state."],
        "verify": {"assertions": ["All criteria verified"]},
        "steps": ["archive-step"],
    }
    flags = {}
    workflow_plan = {
        "specify": {"active": ["step-a"], "filtered": []},
        "complete": {"active": ["archive-step"], "filtered": []},
    }

    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, "feature", schema)
    # Write the include file
    (workflows_dir / "_complete-phase.yaml").write_text(
        yaml.safe_dump(complete_phase, sort_keys=False)
    )
    for sid in ("step-a", "archive-step"):
        _write_step_contract(
            contracts_dir, sid,
            {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": []},
        )
    _make_project_yaml(tmp_path, [])

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_path = _make_state_yaml(tmp_path, "feature", flags, workflow_plan)

    generate_plan(str(state_path))
    plan = yaml.safe_load((state_path.parent / "plan.yaml").read_text())

    phase_names = [p["name"] for p in plan["phases"]]
    assert "complete" in phase_names, (
        f"Expected 'complete' phase from include resolution, got phases: {phase_names}"
    )

    complete_p = next(p for p in plan["phases"] if p["name"] == "complete")
    assert complete_p["goal"] == "Archive and complete."
    step_ids = [s["id"] for s in complete_p["steps"]]
    assert step_ids == ["archive-step"]
