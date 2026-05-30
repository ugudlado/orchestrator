"""`orchestrator learn` — thin driver for config/workflows/workflow-learner.yaml."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from orchestrator_next.parser import load_state
from orchestrator_next.rework import inline_script_env


def _ensure_orchestrator_home() -> None:
    if os.environ.get("ORCHESTRATOR_HOME"):
        return
    here = Path(__file__).resolve().parent.parent
    if (here / "config").is_dir():
        os.environ["ORCHESTRATOR_HOME"] = str(here)


def main(argv: list[str] | None = None) -> int:
    from orchestrator_next.operator_workflow import load_step_params, run_script_workflow

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: orchestrator learn <state.yaml>", file=sys.stderr)
        return 3

    state_yaml = str(Path(args[0]).resolve())
    if not Path(state_yaml).is_file():
        print(f"error: state.yaml not found: {state_yaml}", file=sys.stderr)
        return 1

    _ensure_orchestrator_home()
    state = load_state(state_yaml)
    change_id = state.change_id or Path(state_yaml).parent.name
    params = load_step_params("gather-learn-metrics")
    scope = os.environ.get("LEARN_SCOPE") or params.get("LEARN_SCOPE", "all")

    env = inline_script_env(state, state_yaml)

    print(f"[workflow-learner] change={change_id} state={state_yaml}", file=sys.stderr)
    code = run_script_workflow("workflow-learner", env)
    if code != 0:
        return code

    print(
        f"\n[workflow-learner] Metrics prep done. Spawn workflow-learner agent with:\n"
        f"  state_yaml_path={state_yaml}\n"
        f"  scope={scope}\n",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
