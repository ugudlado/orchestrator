"""Tests for orchestrator_next.parser — phase_nodes read path and optional input parsing."""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def steps_dir(tmp_path):
    """Create a temp steps directory and set env override."""
    d = tmp_path / "steps"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def set_override(steps_dir, monkeypatch):
    """Point load_contract_for_step to the temp steps dir."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_contract(steps_dir, step_id: str, data: dict):
    """Write a directory-form contract (canonical form since flat-file removed)."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(data))
    if data.get("agent") and not data.get("run"):
        (step_dir / "prompt.md").write_text(data.get("instruction", "placeholder"))


# ---------------------------------------------------------------------------
# ORC-63 T-1: parser.phase_nodes node-shape read path (AC-1, AC-11)
# ---------------------------------------------------------------------------

class TestPhaseNodes:
    """Tests for parser.phase_nodes(state, phase)."""

    def _write_state(self, tmp_path, data: dict):
        p = tmp_path / "state.yaml"
        p.write_text(yaml.dump(data))
        return str(p)

    def test_nodes_block_returned_verbatim(self, tmp_path):
        """A workflow_plan.main.nodes block is returned verbatim."""
        nodes = [
            {"id": "explore", "status": "completed", "agent": "discoverer"},
            {"id": "design", "status": "pending", "agent": "architect"},
        ]
        p = self._write_state(tmp_path, {
            "change_id": "f",
            "phase": "main",
            "workflow_plan": {"main": {"nodes": nodes, "filtered": []}},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state, phase_nodes
        state = load_state(p)
        assert phase_nodes(state, "main") == nodes

    def test_nodes_block_returned_unchanged_any_count(self, tmp_path):
        """A {nodes:[...]} block is returned unchanged regardless of node count."""
        single = [{"id": "only", "status": "in_progress"}]
        p = self._write_state(tmp_path, {
            "change_id": "f",
            "phase": "main",
            "workflow_plan": {"main": {"nodes": single}},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state, phase_nodes
        state = load_state(p)
        assert phase_nodes(state, "main") == single

    def test_phase_nodes_exists(self, tmp_path):
        """parser.phase_nodes is importable (fails today — helper absent)."""
        from orchestrator_next.parser import phase_nodes  # noqa: F401


class TestExtendsBaseRoleLine:
    """SKILL.md with a path `extends` gets a base-role read instruction prepended."""

    def _skill(self, tmp_path, extends=None):
        role = tmp_path / "prompt-packs" / "developer"
        role.mkdir(parents=True, exist_ok=True)
        (role / "prompt.md").write_text("# Developer role\n")
        skill = tmp_path / "pack" / "skills" / "implement"
        skill.mkdir(parents=True, exist_ok=True)
        fm = f"extends: {extends}\n" if extends else ""
        (skill / "SKILL.md").write_text(f"---\nname: implement\n{fm}---\n\n# Body\n")
        return skill / "SKILL.md"

    def test_path_extends_prepends_base_role_line(self, tmp_path):
        from orchestrator_next.parser import _load_prompt_file
        out = _load_prompt_file(self._skill(tmp_path, "../../../prompt-packs/developer"))
        assert out.startswith("Base role: read ")
        assert "prompt-packs/developer/prompt.md" in out.splitlines()[0]
        assert "# Body" in out

    def test_git_extends_and_missing_path_unchanged(self, tmp_path):
        from orchestrator_next.parser import _load_prompt_file
        assert _load_prompt_file(self._skill(tmp_path, "git+git@x:y.git@abc#developer")) == "# Body\n"
        assert _load_prompt_file(self._skill(tmp_path, "../../../prompt-packs/nope")) == "# Body\n"
        assert _load_prompt_file(self._skill(tmp_path)) == "# Body\n"
