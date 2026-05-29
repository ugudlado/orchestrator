"""
ORC-63 T-20: RED tests for the `orchestrator ready` and `orchestrator graph`
read-only subcommands.

Both verbs are read-only — they must leave state.yaml byte-unchanged and write
no DuckDB rows. `orchestrator next` must still return the first ready node.

Fails today: bin/orchestrator rejects `ready`/`graph` (exit 3) and graph.py
does not exist.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")


def _run(args, env=None):
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR] + args,
        capture_output=True, text=True, env=full_env,
    )


def _write_state(tmp_path, nodes, phase="main"):
    """Write a state.yaml in the ORC-63 nodes shape; return its path."""
    contracts = tmp_path / "steps"
    contracts.mkdir(exist_ok=True)
    for n in nodes:
        (contracts / f"{n['id']}.yaml").write_text(yaml.safe_dump({
            "id": n["id"], "agent": "developer", "instruction": "x",
            "inputs": [], "outputs": [], "rules": [],
        }))
    state = {
        "change_id": "orc63-cli",
        "phase": phase,
        "repo_root": str(tmp_path),
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": [],
    }
    p = tmp_path / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(p), str(contracts)


def _env(contracts):
    # No ORCHESTRATOR_HOME / METRICS_DB → offline mode, no DuckDB rows.
    return {
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": contracts,
        "ORCHESTRATOR_HOME": "",
        "METRICS_DB": "",
    }


def test_ready_prints_json_array_of_ready_node_ids(tmp_path):
    """`orchestrator ready <state.yaml>` prints a JSON array of ready node ids
    and exits 0."""
    nodes = [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
        {"id": "c", "status": "pending"},
    ]
    sp, contracts = _write_state(tmp_path, nodes)
    result = _run(["ready", sp], env=_env(contracts))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    parsed = json.loads(result.stdout)
    assert parsed == ["b"], f"expected ['b'], got {parsed!r}"


def test_graph_prints_mermaid_flowchart(tmp_path):
    """`orchestrator graph <state.yaml>` prints a Mermaid flowchart TD with one
    entry per node labelled by status and exits 0."""
    nodes = [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
    ]
    sp, contracts = _write_state(tmp_path, nodes)
    result = _run(["graph", sp], env=_env(contracts))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout
    assert "flowchart TD" in out
    assert "a" in out and "b" in out
    assert "completed" in out and "pending" in out


def test_ready_and_graph_leave_state_byte_unchanged(tmp_path):
    """Both verbs are read-only — state.yaml is byte-identical afterward."""
    nodes = [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
    ]
    sp, contracts = _write_state(tmp_path, nodes)
    before = open(sp, "rb").read()
    _run(["ready", sp], env=_env(contracts))
    assert open(sp, "rb").read() == before, "`ready` mutated state.yaml"
    _run(["graph", sp], env=_env(contracts))
    assert open(sp, "rb").read() == before, "`graph` mutated state.yaml"


def test_next_still_returns_first_ready_node(tmp_path):
    """`orchestrator next` still returns the action for the first ready node."""
    nodes = [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
    ]
    sp, contracts = _write_state(tmp_path, nodes)
    result = _run(["next", sp], env=_env(contracts))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    action = json.loads(result.stdout)
    assert action["step_id"] == "b"
