"""Run the rework workflow (config/workflows/rework.yaml) as a single inline step.

`orchestrator rework <change-id>` resolves state.yaml (including archive paths)
and executes ``ticket-rework`` without seeding a feature or entering the full
run-workflow loop (which would halt on archived/completed features).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from orchestrator_next.operator_workflow import workflow_step_ids
from orchestrator_next.parser import load_contract_for_step, load_state
from orchestrator_next.step_env import inline_script_env
from orchestrator_next.step_runner import run_step_subprocess

_REWORK_STEP_ID = "ticket-rework"
_REWORK_SCHEMA = "rework"


def rework_step_ids() -> list[str]:
    """Ordered step ids from config/workflows/rework.yaml."""
    return workflow_step_ids(_REWORK_SCHEMA)




def run_rework(state_yaml_path: str) -> int:
    """Execute ticket-rework for the given state.yaml. Returns shell exit code."""
    path = Path(state_yaml_path)
    if not path.is_file():
        print(f"error: state.yaml not found: {state_yaml_path}", file=sys.stderr)
        return 1

    expected = rework_step_ids()
    if expected != [_REWORK_STEP_ID]:
        print(
            f"error: rework workflow must contain only {_REWORK_STEP_ID!r}, got {expected}",
            file=sys.stderr,
        )
        return 3

    state = load_state(str(path))
    contract = load_contract_for_step(_REWORK_STEP_ID, str(path))
    if not contract.run and not contract.main:
        print(f"error: {_REWORK_STEP_ID} contract has no run: or main:", file=sys.stderr)
        return 3

    proc = run_step_subprocess(
        _REWORK_STEP_ID,
        contract,
        inline_script_env(
            state,
            str(path),
            action_env={
                "ORCHESTRATOR_STEP_ID": _REWORK_STEP_ID,
                "ORCHESTRATOR_ATTEMPT": "1",
            },
        ),
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m orchestrator_next.rework <state.yaml>", file=sys.stderr)
        return 3
    return run_rework(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
