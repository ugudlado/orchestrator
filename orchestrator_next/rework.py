"""Run the rework workflow (config/workflows/rework.yaml) as a single inline step.

`orchestrator rework <change-id>` resolves state.yaml (including archive paths)
and executes ``ticket-rework`` without seeding a feature or entering the full
run-workflow loop (which would halt on archived/completed features).
"""
from __future__ import annotations

import sys
from pathlib import Path

from orchestrator_next.operator_workflow import run_script_step, workflow_step_ids

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

    env = {
        "STATE_YAML_PATH": str(path),
        "ORCHESTRATOR_STATE_YAML_PATH": str(path),
        "ORCHESTRATOR_STEP_ID": _REWORK_STEP_ID,
        "ORCHESTRATOR_ATTEMPT": "1",
    }
    return run_script_step(_REWORK_STEP_ID, env)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m orchestrator_next.rework <state.yaml>", file=sys.stderr)
        return 3
    return run_rework(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
