"""Tests for orchestrator_next.generate_plan."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.generate_plan import generate_plan  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_explicit_config_for_home_fixtures(monkeypatch):
    """These tests install schemas under ORCHESTRATOR_HOME — ORCHESTRATOR_CONFIG wins."""
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)


def _make_state_yaml(
    tmp_path: Path,
    schema: str,
    workflow_plan: dict,
) -> Path:
    state = {
        "change_id": "test-feature",
        "slug": "test-feature",
        "schema": schema,
        "status": "active",
        "repo_root": str(tmp_path),
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


def _setup_home(tmp_path, schema_name, schema):
    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    _make_schema_yaml(workflows_dir, schema_name, schema)
    return home


def test_light_flag_drops_filtered_steps(tmp_path, monkeypatch):
    """Only active steps appear in nodes — filtered ones stay in filtered list."""
    schema = {
        "name": "feature", "version": 1,
        "steps": ["explore if discovery", "ux-design if ux_design", "design-and-draft-artifacts"],
    }
    workflow_plan = {
        "specify": {
            "active": ["design-and-draft-artifacts"],
            "filtered": [
                {"id": "explore", "reason": "flag discovery=false"},
                {"id": "ux-design", "reason": "flag ux_design=false"},
            ],
        }
    }
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)

    generate_plan(str(state_path))

    state = yaml.safe_load(state_path.read_text())
    step_ids = [n["id"] for n in state["workflow_plan"]["specify"]["nodes"]]
    assert step_ids == ["design-and-draft-artifacts"]


def test_byte_stable_output(tmp_path, monkeypatch):
    """Running generate_plan twice on the same state.yaml produces identical bytes."""
    schema = {
        "name": "feature", "version": 1,
        "steps": ["step-a", "step-b"],
    }
    workflow_plan = {"specify": {"active": ["step-a", "step-b"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)

    generate_plan(str(state_path))
    first = state_path.read_bytes()
    generate_plan(str(state_path))
    second = state_path.read_bytes()

    assert first == second


def test_phase_verify_attached_to_phase_block(tmp_path, monkeypatch):
    """verify block from schema phase is a sibling of `nodes`, not on any node."""
    schema = {
        "name": "feature", "version": 1,
        "verify": {"assertions": ["design.md exists"]},
        "steps": ["step-first", "step-last"],
    }
    workflow_plan = {"specify": {"active": ["step-first", "step-last"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)

    generate_plan(str(state_path))
    state = yaml.safe_load(state_path.read_text())
    phase_block = state["workflow_plan"]["specify"]

    for n in phase_block["nodes"]:
        assert "verify" not in n
    assert phase_block["verify"]["assertions"] == ["design.md exists"]


def test_promotes_state_to_nodes_shape_no_plan_yaml(tmp_path, monkeypatch):
    """After generate_plan, workflow_plan.main is {nodes, filtered} and no plan.yaml."""
    schema = {
        "name": "feature", "version": 1,
        "steps": ["step-a", "step-b"],
    }
    workflow_plan = {"main": {"active": ["step-a", "step-b"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)

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


def test_linear_schema_synthesizes_implicit_chain_depends_on(tmp_path, monkeypatch):
    """A linear schema yields implicit-chain depends_on on each node's predecessor."""
    schema = {
        "name": "feature", "version": 1,
        "steps": ["s1", "s2", "s3"],
    }
    workflow_plan = {"main": {"active": ["s1", "s2", "s3"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)
    generate_plan(str(state_path))

    nodes = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]["nodes"]
    by_id = {n["id"]: n for n in nodes}
    assert not by_id["s1"].get("depends_on")
    assert by_id["s2"].get("depends_on") == ["s1"]
    assert by_id["s3"].get("depends_on") == ["s2"]


def test_explicit_depends_on_lands_on_node(tmp_path, monkeypatch):
    """An explicit depends_on on a dict-form schema step entry lands on its node."""
    schema = {
        "name": "feature", "version": 1,
        "steps": ["explore", {"id": "design", "depends_on": ["explore"]}],
    }
    workflow_plan = {"main": {"active": ["explore", "design"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)
    generate_plan(str(state_path))

    nodes = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]["nodes"]
    by_id = {n["id"]: n for n in nodes}
    assert by_id["design"]["depends_on"] == ["explore"]


def test_cyclic_edges_raise_and_keep_pre_promotion_shape(tmp_path, monkeypatch):
    """Cyclic depends_on edges raise ValueError; state.yaml keeps its pre-promotion shape."""
    schema = {
        "name": "feature", "version": 1,
        "steps": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
    }
    workflow_plan = {"main": {"active": ["a", "b"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)

    with pytest.raises(ValueError, match="cycle"):
        generate_plan(str(state_path))

    main = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]
    assert "active" in main
    assert "nodes" not in main


def test_depends_on_to_filtered_step_dropped_with_warning(tmp_path, monkeypatch, capsys):
    """A depends_on edge targeting a filtered step is dropped with a stderr warning."""
    schema = {
        "name": "feature", "version": 1,
        "steps": ["ux-design", {"id": "design", "depends_on": ["ux-design"]}],
    }
    workflow_plan = {"main": {
        "active": ["design"],
        "filtered": [{"id": "ux-design", "reason": "flag ux_design=false"}],
    }}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)
    generate_plan(str(state_path))

    captured = capsys.readouterr()
    assert "ux-design" in captured.err
    nodes = yaml.safe_load(state_path.read_text())["workflow_plan"]["main"]["nodes"]
    assert not {n["id"]: n for n in nodes}["design"].get("depends_on")


def test_depends_on_unknown_id_raises(tmp_path, monkeypatch):
    """A depends_on to an unknown id raises ValueError."""
    schema = {
        "name": "feature", "version": 1,
        "steps": [{"id": "design", "depends_on": ["nonexistent"]}],
    }
    workflow_plan = {"main": {"active": ["design"], "filtered": []}}
    home = _setup_home(tmp_path, "feature", schema)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    state_path = _make_state_yaml(tmp_path, "feature", workflow_plan)

    with pytest.raises(ValueError):
        generate_plan(str(state_path))
