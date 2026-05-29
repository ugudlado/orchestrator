"""
Tests for orchestrator_next.generate_plan.

Tests per design.md § Testing Strategy:
  test_light_flag_drops_filtered_steps
  test_rule_merge_precedence
  test_byte_stable_output
  test_repeat_until_preserved
  test_phase_verify_attached_to_last_step
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

    # Assert — workflow_plan promoted in place; no plan.yaml.
    assert not (state_path.parent / "plan.yaml").exists()
    state = yaml.safe_load(state_path.read_text())
    specify_phase = state["workflow_plan"]["specify"]
    step_ids = [n["id"] for n in specify_phase["nodes"]]
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

    state = yaml.safe_load(state_path.read_text())
    step = state["workflow_plan"]["work"]["nodes"][0]
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

    # Run twice — the promoted state.yaml must be byte-identical (idempotent).
    generate_plan(str(state_path))
    first = state_path.read_bytes()

    generate_plan(str(state_path))
    second = state_path.read_bytes()

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
    state = yaml.safe_load(state_path.read_text())

    step = state["workflow_plan"]["implement"]["nodes"][0]
    assert step["id"] == "execute-next-task"
    assert step.get("repeat_until") == "all_tasks_completed", (
        f"repeat_until not preserved on execute-next-task, got: {step.get('repeat_until')!r}"
    )


# ---------------------------------------------------------------------------
# test_phase_verify_attached_to_last_step
# ---------------------------------------------------------------------------


def test_phase_verify_attached_to_phase_block(tmp_path, monkeypatch):
    """verify block from schema phase is a sibling of `nodes` (ORC-63), not on a node."""
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
    state = yaml.safe_load(state_path.read_text())

    phase_block = state["workflow_plan"]["specify"]
    nodes = phase_block["nodes"]
    assert len(nodes) == 2

    # verify is a phase-level sibling of nodes, not attached to any node.
    for n in nodes:
        assert "verify" not in n, f"node {n['id']} should not carry verify"
    assert "verify" in phase_block, "phase block should carry the verify key"
    assert phase_block["verify"]["assertions"] == ["design.md exists"]


# ===========================================================================
# ORC-63 T-8: generate_plan node promotion + topo-sort cycle detection
# ===========================================================================


def _setup_home(tmp_path, schema_name, schema, contracts):
    """Write schema + contracts under a temp ORCHESTRATOR_HOME; return dirs."""
    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    contracts_dir = home / "config" / "steps"
    contracts_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, schema_name, schema)
    for sid, cdata in contracts.items():
        _write_step_contract(contracts_dir, sid, cdata)
    _make_project_yaml(tmp_path, [])
    return home, contracts_dir


def test_promotes_state_to_nodes_shape_no_plan_yaml(tmp_path, monkeypatch):
    """After generate_plan, state.yaml workflow_plan.main is {nodes, filtered,
    verify} and NO plan.yaml exists on disk."""
    schema = {
        "name": "feature", "version": 1, "rules": [],
        "phases": [{
            "name": "main", "goal": "Do.", "rules": [],
            "steps": ["step-a", "step-b"],
        }],
    }
    contracts = {
        sid: {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": []}
        for sid in ("step-a", "step-b")
    }
    home, contracts_dir = _setup_home(tmp_path, "feature", schema, contracts)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    workflow_plan = {"main": {"active": ["step-a", "step-b"], "filtered": []}}
    state_path = _make_state_yaml(tmp_path, "feature", {}, workflow_plan)

    generate_plan(str(state_path))

    assert not (state_path.parent / "plan.yaml").exists()
    state = yaml.safe_load(state_path.read_text())
    main = state["workflow_plan"]["main"]
    assert "active" not in main
    assert "nodes" in main and "filtered" in main
    nodes = main["nodes"]
    assert [n["id"] for n in nodes] == ["step-a", "step-b"]
    for n in nodes:
        assert n["status"] == "pending"
        for key in ("agent", "goal", "inputs", "outputs", "rules"):
            assert key in n


def test_linear_schema_synthesizes_implicit_chain_depends_on(tmp_path, monkeypatch):
    """A linear schema yields one node per step in active order, each with an
    implicit-chain depends_on on its predecessor."""
    schema = {
        "name": "feature", "version": 1, "rules": [],
        "phases": [{
            "name": "main", "goal": "Do.", "rules": [],
            "steps": ["s1", "s2", "s3"],
        }],
    }
    contracts = {
        sid: {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": []}
        for sid in ("s1", "s2", "s3")
    }
    home, contracts_dir = _setup_home(tmp_path, "feature", schema, contracts)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    workflow_plan = {"main": {"active": ["s1", "s2", "s3"], "filtered": []}}
    state_path = _make_state_yaml(tmp_path, "feature", {}, workflow_plan)
    generate_plan(str(state_path))

    nodes = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]["nodes"]
    by_id = {n["id"]: n for n in nodes}
    # First node: no depends_on (or empty)
    assert not by_id["s1"].get("depends_on")
    assert by_id["s2"].get("depends_on") == ["s1"]
    assert by_id["s3"].get("depends_on") == ["s2"]


def test_explicit_depends_on_lands_on_node(tmp_path, monkeypatch):
    """An explicit depends_on on a dict-form schema step entry lands on its node."""
    schema = {
        "name": "feature", "version": 1, "rules": [],
        "phases": [{
            "name": "main", "goal": "Do.", "rules": [],
            "steps": ["explore", {"id": "design", "depends_on": ["explore"]}],
        }],
    }
    contracts = {
        sid: {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": []}
        for sid in ("explore", "design")
    }
    home, contracts_dir = _setup_home(tmp_path, "feature", schema, contracts)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    workflow_plan = {"main": {"active": ["explore", "design"], "filtered": []}}
    state_path = _make_state_yaml(tmp_path, "feature", {}, workflow_plan)
    generate_plan(str(state_path))

    nodes = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]["nodes"]
    by_id = {n["id"]: n for n in nodes}
    assert by_id["design"]["depends_on"] == ["explore"]


def test_cyclic_edges_raise_and_keep_pre_promotion_shape(tmp_path, monkeypatch):
    """Cyclic depends_on edges raise non-zero with the cycle path; state.yaml
    keeps its pre-promotion (active) shape."""
    schema = {
        "name": "feature", "version": 1, "rules": [],
        "phases": [{
            "name": "main", "goal": "Do.", "rules": [],
            "steps": [
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            ],
        }],
    }
    contracts = {
        sid: {"id": sid, "agent": "inline", "inputs": [], "outputs": [], "rules": []}
        for sid in ("a", "b")
    }
    home, contracts_dir = _setup_home(tmp_path, "feature", schema, contracts)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    workflow_plan = {"main": {"active": ["a", "b"], "filtered": []}}
    state_path = _make_state_yaml(tmp_path, "feature", {}, workflow_plan)

    with pytest.raises(ValueError, match="cycle"):
        generate_plan(str(state_path))

    # state.yaml unchanged — still has the active shape
    main = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]
    assert "active" in main
    assert "nodes" not in main


def test_depends_on_to_filtered_step_dropped_with_warning(tmp_path, monkeypatch, capsys):
    """A depends_on edge targeting a filtered step is dropped with a stderr warning."""
    schema = {
        "name": "feature", "version": 1, "rules": [],
        "phases": [{
            "name": "main", "goal": "Do.", "rules": [],
            "steps": ["ux-design", {"id": "design", "depends_on": ["ux-design"]}],
        }],
    }
    contracts = {
        "design": {"id": "design", "agent": "inline", "inputs": [], "outputs": [], "rules": []},
    }
    home, contracts_dir = _setup_home(tmp_path, "feature", schema, contracts)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    # ux-design is filtered out of the plan
    workflow_plan = {"main": {
        "active": ["design"],
        "filtered": [{"id": "ux-design", "reason": "flag ux_design=false"}],
    }}
    state_path = _make_state_yaml(tmp_path, "feature", {}, workflow_plan)
    generate_plan(str(state_path))

    captured = capsys.readouterr()
    assert "ux-design" in captured.err
    nodes = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]["nodes"]
    by_id = {n["id"]: n for n in nodes}
    # The edge to the filtered step is dropped
    assert not by_id["design"].get("depends_on")


def test_depends_on_unknown_id_raises(tmp_path, monkeypatch):
    """A depends_on to an unknown (not filtered, not in plan) id raises."""
    schema = {
        "name": "feature", "version": 1, "rules": [],
        "phases": [{
            "name": "main", "goal": "Do.", "rules": [],
            "steps": [{"id": "design", "depends_on": ["nonexistent"]}],
        }],
    }
    contracts = {
        "design": {"id": "design", "agent": "inline", "inputs": [], "outputs": [], "rules": []},
    }
    home, contracts_dir = _setup_home(tmp_path, "feature", schema, contracts)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    workflow_plan = {"main": {"active": ["design"], "filtered": []}}
    state_path = _make_state_yaml(tmp_path, "feature", {}, workflow_plan)

    with pytest.raises(ValueError):
        generate_plan(str(state_path))
