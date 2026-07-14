"""Integration canary: the LOOP itself advances and terminates.

The advisor's load-bearing gap: every other test calls run_loop's sub-functions
directly. This drives run_loop() across a 2-node plan and asserts the one
behavior that defines a working driver — it advances from step 1 to step 2 and
exits 1 (workflow complete). A loop that spins on step 1 or crashes on iteration
2 passes every other test and fails here.

Two script steps (no model) keep it deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from orchestrator_next import run_loop  # noqa: E402


def _make_script_contract(contracts_dir: Path, step_id: str) -> None:
    """A directory-form script contract that succeeds and emits empty JSON."""
    d = contracts_dir / step_id
    d.mkdir(parents=True)
    (d / "contract.yaml").write_text(yaml.safe_dump({
        "id": step_id, "version": 2, "run": "script.sh", "outputs": [],
    }))
    script = d / "script.sh"
    script.write_text("#!/usr/bin/env bash\necho '{}'\n")
    script.chmod(0o755)


def test_loop_advances_two_steps_and_completes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)
    (repo / "spec" / "project.yaml").write_text(yaml.safe_dump({
        "version": 1, "project": {"name": "t", "repo": "t", "summary": "s"},
        "quality_bar": {"max_spawn_failures": 3}, "rules": [],
    }))

    contracts = tmp_path / "contracts"
    _make_script_contract(contracts, "step-one")
    _make_script_contract(contracts, "step-two")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))

    # 2-node plan: step-two depends on step-one → the loop MUST advance in order.
    state_dir = repo / ".orchestrator" / "loop"
    state_dir.mkdir(parents=True)
    state_yaml = state_dir / "20260101T000000_feature_state.yaml"
    state_yaml.write_text(yaml.safe_dump({
        "change_id": "loop", "schema": "feature", "version": 1,
        "status": "active", "phase": "main", "repo_root": str(repo),
        "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": [
            {"id": "step-one", "status": "pending"},
            {"id": "step-two", "status": "pending", "depends_on": ["step-one"]},
        ]}},
        "step_history": [],
    }))

    code = run_loop.run_loop(
        str(state_yaml), repo_root=str(repo), models_yaml="",
    )

    # 1. The loop terminated cleanly (workflow complete).
    assert code == 1, f"expected exit 1 (complete), got {code}"

    # 2. BOTH steps ran — the loop advanced past step 1, not spun on it.
    final = yaml.safe_load(state_yaml.read_text())
    hist = final.get("step_history") or []
    completed = [e["step_id"] for e in hist if e.get("status") == "completed"]
    assert "step-one" in completed, f"step-one not completed: {hist}"
    assert "step-two" in completed, f"step-two not completed (loop didn't advance): {hist}"

    # 3. Order: step-one recorded before step-two (dependency honored).
    order = [e["step_id"] for e in hist if e.get("step_id") in ("step-one", "step-two")]
    assert order.index("step-one") < order.index("step-two"), f"out of order: {order}"

    # 4. No infinite re-dispatch: each step recorded exactly once.
    assert order.count("step-one") == 1, f"step-one re-dispatched: {order}"
    assert order.count("step-two") == 1, f"step-two re-dispatched: {order}"
