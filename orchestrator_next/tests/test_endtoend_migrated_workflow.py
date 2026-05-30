"""
ORC-76 T-25: End-to-end smoke tests for the migrated directory layout.

Scenarios:
  1. `orchestrator next` against a synthetic state.yaml with a directory-form
     agent contract → emits action JSON with non-empty instruction (loaded from
     prompt.md) and step_id matching the contract.
  2. `orchestrator next` against a script-kind contract → emits action JSON
     with a resolved `run` path pointing to the script inside the step directory.
  3. Pre-step typed input file missing → exit 2; stderr names the resolved path.
  4. Post-step typed output file missing → `orchestrator done` returns
     reason: missing_outputs (exit 3).

AC-1, AC-2, AC-3, AC-4, AC-8, AC-9 (design.md)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers (shared with test_dispatch_typed_inputs.py)
# ---------------------------------------------------------------------------

def _write_agent_contract(
    steps_dir: Path,
    step_id: str,
    contract_data: dict,
    prompt_text: str = "Architect instruction text.\n",
) -> Path:
    """Write a directory-form agent contract with a prompt.md sibling."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(contract_data))
    (step_dir / "prompt.md").write_text(prompt_text)
    return step_dir


def _write_script_contract(
    steps_dir: Path,
    step_id: str,
    contract_data: dict,
    script_text: str = "#!/bin/sh\necho done\n",
) -> Path:
    """Write a directory-form script contract with a script.sh sibling."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    contract_data_with_run = {**contract_data, "run": "script.sh"}
    (step_dir / "contract.yaml").write_text(yaml.dump(contract_data_with_run))
    script_path = step_dir / "script.sh"
    script_path.write_text(script_text)
    script_path.chmod(0o755)
    return step_dir


def _make_state_yaml(
    state_dir: Path,
    change_id: str,
    worktree_artifact_dir: str,
    phase: str,
    nodes: list[dict],
    step_history: list | None = None,
    worktree_path: str | None = None,
) -> str:
    """Write a state.yaml with the ORC-63 nodes-shape workflow_plan.

    worktree_path: root used by record.py as the base for typed output path
    resolution. Set this to the parent of spec/changes/ when testing typed
    output checks so paths resolve correctly.
    """
    state = {
        "change_id": change_id,
        "slug": change_id,
        "schema": "feature",
        "status": "active",
        "repo_root": str(state_dir),
        "worktree_artifact_dir": worktree_artifact_dir,
        "flags": {},
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "phase": phase,
        "step_history": step_history or [],
    }
    if worktree_path is not None:
        state["worktree_path"] = worktree_path
    path = state_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _node(step_id: str, status: str = "pending", **extra) -> dict:
    node = {
        "id": step_id,
        "status": status,
        "agent": "architect",
        "goal": "Test goal.",
        "inputs": [],
        "outputs": [],
        "rules": [],
    }
    node.update(extra)
    return node


# ---------------------------------------------------------------------------
# Scenario 1: agent contract → action has non-empty instruction from prompt.md
# ---------------------------------------------------------------------------

class TestAgentContractDispatch:
    """Directory-form agent contract → dispatch emits action with instruction
    loaded from prompt.md and step_id matching the contract id."""

    def test_agent_contract_emits_instruction(self, tmp_path, monkeypatch):
        """Agent contract directory form → action['instruction'] non-empty."""
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        prompt = "## Architect\n\nRun the explore phase and produce discovery.md.\n"
        _write_agent_contract(steps_dir, "explore", {
            "id": "explore",
            "version": 1,
            "kind": "agent",
            "agent": "discoverer",
            "inputs": [],
            "outputs": ["discovery_result"],
            "rules": [],
        }, prompt_text=prompt)

        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

        sp = _make_state_yaml(
            state_dir,
            change_id="orc-smoke",
            worktree_artifact_dir=str(artifact_dir),
            phase="main",
            nodes=[_node("explore")],
        )

        from orchestrator_next.dispatch import dispatch
        from orchestrator_next.parser import load_state

        action, code = dispatch(load_state(sp), sp)

        assert code == 0, f"Expected exit 0, got {code}"
        assert action.get("step_id") == "explore", (
            f"Expected step_id='explore', got {action.get('step_id')!r}"
        )
        assert action.get("instruction"), "Expected non-empty instruction in action"
        assert "discover" in action["instruction"].lower() or "architect" in action["instruction"].lower(), (
            f"Expected instruction content from prompt.md, got: {action['instruction'][:100]!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 2: script contract → action has resolved `run` path in step dir
# ---------------------------------------------------------------------------

class TestScriptContractDispatch:
    """Directory-form script contract → dispatch emits action with `run`
    resolved to the absolute path of script.sh inside the step directory."""

    def test_script_contract_resolves_run_path(self, tmp_path, monkeypatch):
        """Script contract directory form → action['run'] is absolute path to script.sh."""
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        _write_script_contract(steps_dir, "expand-plan", {
            "id": "expand-plan",
            "version": 1,
            "kind": "script",
            "inputs": [],
            "outputs": [],
            "rules": [],
        })

        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

        sp = _make_state_yaml(
            state_dir,
            change_id="orc-smoke",
            worktree_artifact_dir=str(artifact_dir),
            phase="main",
            nodes=[_node("expand-plan", agent=None)],
        )

        from orchestrator_next.dispatch import dispatch
        from orchestrator_next.parser import load_state

        action, code = dispatch(load_state(sp), sp)

        # Script steps may return inline (code 0, no agent key) or agent (code 0, agent key).
        # Either way the run path must be an absolute path inside the step directory.
        assert code == 0, f"Expected exit 0, got {code}"
        run_path = action.get("run") or ""
        assert run_path, f"Expected 'run' in action, got: {list(action.keys())}"
        assert os.path.isabs(run_path), f"Expected absolute run path, got {run_path!r}"
        assert run_path.endswith("script.sh"), (
            f"Expected run path to end in 'script.sh', got {run_path!r}"
        )
        assert "expand-plan" in run_path, (
            f"Expected run path inside expand-plan/ dir, got {run_path!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 3: pre-step typed input file missing → exit 2, stderr names path
# ---------------------------------------------------------------------------

class TestPreStepMissingTypedInput:
    """When a required typed input file is absent, dispatch exits 2 and
    stderr includes the resolved absolute path so operators can diagnose."""

    def test_missing_required_typed_input_exits_2(self, tmp_path, monkeypatch):
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Do NOT create the discovery.md file
        _write_agent_contract(steps_dir, "design-and-draft-artifacts", {
            "id": "design-and-draft-artifacts",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"},
            ],
            "outputs": [],
            "rules": [],
        })
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

        sp = _make_state_yaml(
            state_dir,
            change_id="orc-smoke",
            worktree_artifact_dir=str(artifact_dir),
            phase="main",
            nodes=[_node("design-and-draft-artifacts")],
        )

        from orchestrator_next.dispatch import dispatch
        from orchestrator_next.parser import load_state

        action, code = dispatch(load_state(sp), sp)

        assert code == 2, f"Expected exit 2 for missing typed input, got {code}"

    def test_missing_required_typed_input_stderr_names_path(
        self, tmp_path, monkeypatch, capsys
    ):
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        _write_agent_contract(steps_dir, "design-and-draft-artifacts", {
            "id": "design-and-draft-artifacts",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"},
            ],
            "outputs": [],
            "rules": [],
        })
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

        sp = _make_state_yaml(
            state_dir,
            change_id="orc-smoke",
            worktree_artifact_dir=str(artifact_dir),
            phase="main",
            nodes=[_node("design-and-draft-artifacts")],
        )

        from orchestrator_next.dispatch import dispatch
        from orchestrator_next.parser import load_state

        dispatch(load_state(sp), sp)
        err = capsys.readouterr().err

        assert "spec/changes/orc-smoke/discovery.md" in err, (
            f"Expected resolved path in stderr, got:\n{err}"
        )


# ---------------------------------------------------------------------------
# Scenario 4: post-step typed output file missing → orchestrator done exits 3
# ---------------------------------------------------------------------------

class TestPostStepMissingTypedOutput:
    """When a required typed output file is absent after step completion,
    orchestrator done returns reason: missing_outputs (exit code 3)."""

    def test_missing_typed_output_returns_exit_3(self, tmp_path, monkeypatch):
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        _write_agent_contract(steps_dir, "design-and-draft-artifacts", {
            "id": "design-and-draft-artifacts",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [],
            "outputs": [
                {"name": "design", "path": "spec/changes/<slug>/design.md"},
            ],
            "rules": [],
        })
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

        # Create a state.yaml with design-and-draft-artifacts in_progress
        sp = _make_state_yaml(
            state_dir,
            change_id="orc-smoke",
            worktree_artifact_dir=str(artifact_dir),
            phase="main",
            nodes=[_node("design-and-draft-artifacts", status="in_progress")],
            worktree_path=str(artifact_dir),
        )

        from orchestrator_next.record import record

        # Report step completed but do NOT create design.md → should get missing_outputs
        payload = {
            "step_id": "design-and-draft-artifacts",
            "phase": "main",
            "status": "completed",
            "agent": "architect",
            "outputs": {"design_direction": "approach-a"},
            "evidence": {},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        result, code = record(sp, payload)

        assert code == 3, (
            f"Expected exit 3 (missing_outputs) when typed output file absent, got {code}. "
            f"Result: {result}"
        )
        assert result.get("reason") == "missing_outputs", (
            f"Expected reason='missing_outputs', got {result.get('reason')!r}"
        )
        missing = result.get("missing_outputs", [])
        assert any("design" in str(m) for m in missing), (
            f"Expected 'design' in missing_outputs, got {missing!r}"
        )

    def test_present_typed_output_allows_completion(self, tmp_path, monkeypatch):
        """When the typed output file exists, orchestrator done exits 0."""
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Create the design.md output file
        design_dir = artifact_dir / "spec" / "changes" / "orc-smoke"
        design_dir.mkdir(parents=True)
        (design_dir / "design.md").write_text("# Design\n## Acceptance Criteria\n- AC-1\n")

        _write_agent_contract(steps_dir, "design-and-draft-artifacts", {
            "id": "design-and-draft-artifacts",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [],
            "outputs": [
                {"name": "design", "path": "spec/changes/<slug>/design.md"},
            ],
            "rules": [],
        })
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

        sp = _make_state_yaml(
            state_dir,
            change_id="orc-smoke",
            worktree_artifact_dir=str(artifact_dir),
            phase="main",
            nodes=[_node("design-and-draft-artifacts", status="in_progress")],
            worktree_path=str(artifact_dir),
        )

        from orchestrator_next.record import record

        payload = {
            "step_id": "design-and-draft-artifacts",
            "phase": "main",
            "status": "completed",
            "agent": "architect",
            "outputs": {"design_direction": "approach-a"},
            "evidence": {},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        result, code = record(sp, payload)

        assert code == 0, (
            f"Expected exit 0 when typed output file exists, got {code}. Result: {result}"
        )
