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

import yaml

from orchestrator_next.parser import State, load_contract_for_step, load_state

_REWORK_STEP_ID = "ticket-rework"
_REWORK_SCHEMA = "rework"


def _orchestrator_home() -> Path:
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if not home:
        raise EnvironmentError("ORCHESTRATOR_HOME is not set")
    return Path(home)


def rework_step_ids() -> list[str]:
    """Ordered step ids from config/workflows/rework.yaml."""
    path = _orchestrator_home() / "config" / "workflows" / f"{_REWORK_SCHEMA}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"workflow not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    steps: list[str] = []
    for entry in raw.get("steps") or []:
        if isinstance(entry, dict):
            steps.append(str(entry.get("id", "")))
        else:
            steps.append(str(entry))
    return [s for s in steps if s]


def inline_script_env(state: State, state_yaml_path: str) -> dict[str, str]:
    """Match bin/orchestrator _inline_script_env for step scripts."""
    raw = state.raw or {}
    env = {
        **os.environ,
        "STATE_YAML_PATH": state_yaml_path,
        "REPO_ROOT": state.repo_root,
        "ORCHESTRATOR_CHANGE_ID": state.change_id,
        "ORCHESTRATOR_PHASE": state.phase or "main",
        "ORCHESTRATOR_STEP_ID": _REWORK_STEP_ID,
        "ORCHESTRATOR_ATTEMPT": "1",
        "ORCHESTRATOR_WORKFLOW_DIR": state.workflow_dir,
        "ORCHESTRATOR_REPO_ROOT": state.repo_root,
        "ORCHESTRATOR_STATE_YAML_PATH": state_yaml_path,
        "ORCHESTRATOR_WORKTREE_ARTIFACT_DIR": state.worktree_artifact_dir,
    }
    change_id = state.change_id or raw.get("slug") or ""
    if change_id:
        env["CHANGE_ID"] = change_id
    branch = raw.get("branch") or ""
    if branch:
        env["BRANCH"] = branch
    worktree = raw.get("worktree_path") or ""
    if worktree:
        worktree = os.path.expanduser(str(worktree))
        env["WORKTREE_PATH"] = worktree
        env["WORKTREE_ROOT"] = worktree
        env["ORCHESTRATOR_WORKFLOW_DIR"] = worktree
    archive_path = raw.get("archive_path") or ""
    if archive_path:
        env["ARCHIVE_PATH"] = str(archive_path)
    repo_root_raw = raw.get("repo_root") or ""
    if repo_root_raw and not env.get("REPO_ROOT"):
        env["REPO_ROOT"] = str(repo_root_raw)
    return env


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
    if not contract.run:
        print(f"error: {_REWORK_STEP_ID} contract has no run:", file=sys.stderr)
        return 3

    run_path = contract.run
    if not run_path or not os.path.isfile(run_path):
        print(f"error: script not found: {run_path}", file=sys.stderr)
        return 3

    proc = subprocess.run(
        ["bash", run_path],
        env=inline_script_env(state, str(path)),
        capture_output=True,
        text=True,
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
