"""`orchestrator telemetry` — thin driver for config/workflows/telemetry.yaml."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _default_repo_root() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except OSError:
        pass
    return str(Path.cwd())


from orchestrator_next.operator_workflow import ensure_orchestrator_home, run_script_workflow
from orchestrator_next.step_env import operator_script_env


def main() -> int:
    ensure_orchestrator_home()
    repo = _default_repo_root()
    if not Path(repo, "spec", "project.yaml").is_file():
        print(f"error: spec/project.yaml not found under {repo}", file=sys.stderr)
        return 7

    return run_script_workflow("telemetry", operator_script_env(repo))


if __name__ == "__main__":
    raise SystemExit(main())
