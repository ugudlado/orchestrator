"""Run operator workflows (script-only steps).

Used by `orchestrator telemetry`.
The CLI is a thin driver; step `params` in contract.yaml supply defaults (overridable via env).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from orchestrator_next.parser import load_contract_for_step, load_state
from orchestrator_next.step_env import inline_script_env, operator_script_env
from orchestrator_next.step_runner import apply_step_paths, build_step_command


def ensure_orchestrator_home() -> None:
    """Set ORCHESTRATOR_HOME from this package when unset."""
    if os.environ.get("ORCHESTRATOR_HOME"):
        return
    here = Path(__file__).resolve().parent.parent
    if (here / "config").is_dir():
        os.environ["ORCHESTRATOR_HOME"] = str(here)


def orchestrator_home() -> Path:
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if not home:
        raise EnvironmentError("ORCHESTRATOR_HOME is not set")
    return Path(home)


def _contract_path(step_id: str) -> Path:
    return orchestrator_home() / "config" / "steps" / step_id / "contract.yaml"


def load_step_params(step_id: str) -> dict[str, str]:
    """Read `params:` from step contract.yaml as string env defaults."""
    path = _contract_path(step_id)
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    params = raw.get("params") or {}
    return {str(k): str(v) for k, v in params.items()}


def _workflow_entries(schema: str) -> list[tuple[str, dict[str, str]]]:
    """Return (step_id, workflow-level param overrides) in order."""
    path = orchestrator_home() / "config" / "workflows" / f"{schema}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"workflow not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, dict[str, str]]] = []
    for entry in raw.get("steps") or []:
        if isinstance(entry, dict):
            step_id = str(entry.get("id", ""))
            overrides = entry.get("params") or {}
            out.append((step_id, {str(k): str(v) for k, v in overrides.items()}))
        else:
            out.append((str(entry), {}))
    return [(s, p) for s, p in out if s]


def workflow_step_ids(schema: str) -> list[str]:
    return [step_id for step_id, _ in _workflow_entries(schema)]


def merge_step_env(step_id: str, env: dict[str, str], workflow_params: dict[str, str] | None = None) -> dict[str, str]:
    """Contract params < workflow overrides < driver env < pre-set os.environ for param keys."""
    contract = load_step_params(step_id)
    wf = workflow_params or {}
    merged = {**contract, **wf, **env}
    for key in {**contract, **wf}:
        if key in os.environ and os.environ[key] != "":
            merged[key] = os.environ[key]
    return merged


def _script_subprocess_env(
    step_id: str,
    env: dict[str, str],
    *,
    workflow_params: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge contract params with orchestrator-built step env."""
    state_yaml = (
        env.get("STATE_YAML_PATH")
        or env.get("ORCHESTRATOR_STATE_YAML_PATH")
        or "/dev/null"
    )
    path = Path(state_yaml)
    if path.is_file():
        state = load_state(str(path))
        base = inline_script_env(
            state,
            str(path),
            action_env={**env, "ORCHESTRATOR_STEP_ID": step_id},
        )
    else:
        repo = env.get("REPO_ROOT") or env.get("ORCHESTRATOR_REPO_ROOT") or ""
        base = operator_script_env(
            repo,
            state_yaml_path=state_yaml,
            step_id=step_id,
        )
    return merge_step_env(step_id, base, workflow_params)


def run_script_step(
    step_id: str,
    env: dict[str, str],
    *,
    workflow_params: dict[str, str] | None = None,
) -> int:
    """Execute a script step contract. Returns subprocess exit code."""
    state_yaml = env.get("STATE_YAML_PATH", "/dev/null")
    contract = load_contract_for_step(step_id, state_yaml)
    if contract.agent and not contract.run:
        print(
            f"error: step {step_id!r} requires an agent host; spawn workflow-learner in the host",
            file=sys.stderr,
        )
        return 3
    if not contract.run and not contract.main:
        print(f"error: step {step_id!r} has no run: or main:", file=sys.stderr)
        return 3
    if contract.run and not os.path.isfile(contract.run):
        print(f"error: script not found: {contract.run}", file=sys.stderr)
        return 3

    step_env = _script_subprocess_env(step_id, env, workflow_params=workflow_params)
    from orchestrator_next.paths import config_root as _config_root
    croot = str(_config_root())
    step_env = apply_step_paths(
        step_env, step_id=step_id, contract=contract, config_root=croot
    )
    cmd = build_step_command(step_id, contract, croot)

    proc = subprocess.run(
        cmd,
        env=step_env,
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


def run_script_workflow(schema: str, env: dict[str, str]) -> int:
    """Run all script steps in workflow order. Stops on first non-zero exit."""
    entries = _workflow_entries(schema)
    if not entries:
        print(f"error: workflow {schema!r} has no steps", file=sys.stderr)
        return 3

    last_code = 0
    state_yaml = env.get("STATE_YAML_PATH", "/dev/null")
    for step_id, wf_params in entries:
        contract = load_contract_for_step(step_id, state_yaml)
        if contract.agent and not contract.run:
            print(
                f"[{schema}] skip agent step {step_id!r} — spawn workflow-learner in the host",
                file=sys.stderr,
            )
            continue
        if not contract.run:
            print(f"warning: skip step {step_id!r} (no run: script)", file=sys.stderr)
            continue
        code = run_script_step(step_id, env, workflow_params=wf_params)
        if code != 0:
            return code
        last_code = code
    return last_code
