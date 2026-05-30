"""`orchestrator telemetry` — thin driver for config/workflows/telemetry.yaml."""
from __future__ import annotations

import os
import subprocess
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


def _ensure_orchestrator_home() -> None:
    if os.environ.get("ORCHESTRATOR_HOME"):
        return
    here = Path(__file__).resolve().parent.parent
    if (here / "config").is_dir():
        os.environ["ORCHESTRATOR_HOME"] = str(here)


def main() -> int:
    from orchestrator_next.operator_workflow import run_script_workflow

    _ensure_orchestrator_home()
    repo = _default_repo_root()
    if not Path(repo, "spec", "project.yaml").is_file():
        print(f"error: spec/project.yaml not found under {repo}", file=sys.stderr)
        return 7

    env = {
        "REPO_ROOT": repo,
        "ORCHESTRATOR_REPO_ROOT": repo,
        "STATE_YAML_PATH": "/dev/null",
    }
    return run_script_workflow("telemetry", env)


if __name__ == "__main__":
    raise SystemExit(main())
