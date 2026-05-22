"""Regression test: an inline step whose script moves/deletes state.yaml.

Bug (observed on orc-78 complete phase, 2026-05-22): a state-deleting inline
script does `rm -rf` of the state directory, then the CLI calls `record.py`
which unconditionally re-opens `state_yaml_path` to read pre-write bytes —
`FileNotFoundError`, traceback, exit 1.

The fix: the CLI records the step completion BEFORE running the script for
state-mutating inline steps. Since orc-79, `complete-workflow` is the sole
state-mutating inline step — it sequences merge → archive (the `rm -rf`) →
worktree-removal, with archive's pre-record protection subsumed into it. So
this test keys its fixture on `complete-workflow`, the step id that now
embodies the record-after-state-deletion crash class.

These tests subprocess the real `bin/orchestrator` binary — the bug lives in the
CLI wrapper's run-script-then-record ordering, not in `dispatch()` itself, so a
unit test on `dispatch()` would not reproduce it.
"""
from __future__ import annotations

import os
import subprocess
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")


def _run(args, env=None):
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR] + args,
        capture_output=True, text=True, env=full_env,
    )


def _write_archive_step_state(tmp_path):
    """Build a state.yaml parked at an inline step whose script deletes the
    state directory — the `complete-workflow` shape.

    Returns (state_yaml_path, contracts_dir).
    """
    contracts = tmp_path / "steps"
    contracts.mkdir(exist_ok=True)

    # An inline step contract: `run:` points at a script that rm -rf's the
    # directory containing state.yaml — exactly what complete-workflow's archive
    # phase does.
    killer = tmp_path / "kill_state_dir.sh"
    killer.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        '# Mimic complete-workflow archive phase: remove the state directory.\n'
        'rm -rf "$(dirname "$STATE_YAML_PATH")"\n'
        '# The real script still prints valid JSON and exits 0.\n'
        'printf \'%s\\n\' \'{"completion_record": {"archived": true}}\'\n'
    )
    killer.chmod(0o755)

    (contracts / "complete-workflow.yaml").write_text(yaml.safe_dump({
        "id": "complete-workflow",
        "run": str(killer),
        "inputs": [],
        "outputs": [],
        "rules": [],
    }))

    state = {
        "change_id": "archive-crash-test",
        "phase": "main",
        "repo_root": str(tmp_path),
        "workflow_plan": {
            "main": {
                "nodes": [
                    {"id": "complete-workflow", "status": "pending"},
                ],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    p = state_dir / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(p), str(contracts)


def _env(contracts):
    return {
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": contracts,
        "ORCHESTRATOR_HOME": "",
        "METRICS_DB": "",
    }


def test_next_does_not_crash_when_inline_step_deletes_state_dir(tmp_path):
    """`orchestrator next` on a state-deleting inline step must not crash.

    RED on the buggy CLI: record.py raises FileNotFoundError re-opening the
    deleted state.yaml, the process prints a traceback and exits 1.
    GREEN after the fix: the step is recorded before its script runs, so `next`
    exits 0 with no traceback.
    """
    sp, contracts = _write_archive_step_state(tmp_path)
    result = _run(["next", sp], env=_env(contracts))

    assert "FileNotFoundError" not in result.stderr, (
        "record.py crashed re-opening the deleted state.yaml:\n" + result.stderr
    )
    assert "Traceback" not in result.stderr, (
        "orchestrator next raised an unhandled exception:\n" + result.stderr
    )
    assert result.returncode == 0, (
        f"expected exit 0 (inline step ran + recorded), got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )


def test_state_deleting_inline_step_is_recorded_before_the_move(tmp_path):
    """The step completion must be recorded into state.yaml BEFORE its script
    deletes the directory — so the recorded entry is durable.

    The killer script copies state.yaml to a sibling `recorded.yaml` snapshot
    just before deleting, letting the test inspect what was persisted at
    delete-time. The fix records first, so the snapshot must show the
    `complete-workflow` step_history entry.
    """
    contracts = tmp_path / "steps"
    contracts.mkdir(exist_ok=True)

    snapshot = tmp_path / "recorded.yaml"
    killer = tmp_path / "snapshot_then_kill.sh"
    killer.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f'cp "$STATE_YAML_PATH" "{snapshot}"\n'
        'rm -rf "$(dirname "$STATE_YAML_PATH")"\n'
        'printf \'%s\\n\' \'{"completion_record": {"archived": true}}\'\n'
    )
    killer.chmod(0o755)

    (contracts / "complete-workflow.yaml").write_text(yaml.safe_dump({
        "id": "complete-workflow",
        "run": str(killer),
        "inputs": [], "outputs": [], "rules": [],
    }))

    state = {
        "change_id": "archive-crash-test",
        "phase": "main",
        "repo_root": str(tmp_path),
        "workflow_plan": {
            "main": {
                "nodes": [{"id": "complete-workflow", "status": "pending"}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    sp = state_dir / "state.yaml"
    sp.write_text(yaml.safe_dump(state, sort_keys=False))

    result = _run(["next", str(sp)], env=_env(str(contracts)))
    assert result.returncode == 0, f"stderr: {result.stderr}"

    assert snapshot.exists(), "killer script did not snapshot state.yaml"
    recorded = yaml.safe_load(snapshot.read_text()) or {}
    history = recorded.get("step_history") or []
    step_ids = [h.get("step_id") for h in history]
    assert "complete-workflow" in step_ids, (
        "complete-workflow was not recorded into state.yaml before the "
        f"script moved it; step_history at delete-time: {step_ids}"
    )
