"""The engine has no hard git/worktree requirement — a workflow dispatches to
completion from a state.yaml that never mentions worktree_path; every read
falls back to repo_root (run_loop.py work_dir, parser.py workflow_dir).

Parallel guard to test_run_loop_ticket_agnostic.py (ORC-125): that one keeps
ticketing out of the engine, this one keeps git/worktree coupling from
deepening (ai-engineer-framework-2026-07.md, Constraints).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.dispatch import dispatch
from orchestrator_next.parser import load_state


def _write_script_step(steps_dir: Path, step_id: str) -> None:
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True)
    (step_dir / "contract.yaml").write_text(yaml.dump({
        "id": step_id,
        "version": 1,
        "kind": "script",
        "run": "script.sh",
        "inputs": [],
        "outputs": [],
        "rules": [],
    }))
    script = step_dir / "script.sh"
    script.write_text("#!/bin/sh\necho done\n")
    script.chmod(0o755)


def _write_state(state_dir: Path, nodes: list[dict]) -> str:
    state = {
        "change_id": "no-git",
        "slug": "no-git",
        "schema": "feature",
        "status": "active",
        "repo_root": str(state_dir),
        "worktree_artifact_dir": str(state_dir / "artifacts"),
        "workflow_plan": {"main": {"nodes": nodes, "filtered": []}},
        "phase": "main",
        "step_history": [],
        # deliberately: no worktree_path, no branch
    }
    path = state_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _node(step_id: str, status: str) -> dict:
    return {"id": step_id, "status": status, "agent": None,
            "goal": "g", "inputs": [], "outputs": [], "rules": []}


def test_workflow_completes_without_worktree_path(tmp_path, monkeypatch):
    steps_dir = tmp_path / "steps"
    _write_script_step(steps_dir, "only-step")
    (tmp_path / "artifacts").mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

    # pending node → dispatches (exit 0), run path resolves with no worktree
    sp = _write_state(tmp_path, [_node("only-step", "pending")])
    action, code = dispatch(load_state(sp), sp)
    assert code == 0
    assert action.get("run", "").endswith("script.sh")

    # completed node → workflow complete (exit 1), still no worktree_path anywhere
    sp = _write_state(tmp_path, [_node("only-step", "completed")])
    action, code = dispatch(load_state(sp), sp)
    assert code == 1
    assert "worktree_path" not in Path(sp).read_text()
