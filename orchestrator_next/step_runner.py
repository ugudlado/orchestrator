"""Resolve and execute workflow step entrypoints (Python main: or bash run:)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from orchestrator_next.parser import StepContract


def step_directory(step_id: str, contract: StepContract, config_root: str) -> Path:
    """Directory for <config_root>/steps/<step_id>/ (authoritative, not dirname of _runner).

    `config_root` is the config/ dir itself (see paths.config_root) — this
    function joins `steps/<id>` directly, NOT `config/steps/<id>`.
    """
    if contract.run and os.path.isabs(contract.run):
        run_dir = Path(contract.run).parent
        if run_dir.name != "_runner":
            return run_dir
    return Path(config_root) / "steps" / step_id


def resolve_main_path(step_dir: Path, main: str, config_root: str) -> Path:
    """Resolve contract ``main:`` to an executable Python file path."""
    raw = Path(main)
    if raw.is_absolute():
        return raw
    if main.startswith("lib/") or main.startswith("../lib/"):
        steps_root = Path(config_root) / "steps"
        return (steps_root / main).resolve()
    return (step_dir / main).resolve()


def apply_step_paths(
    env: dict[str, str],
    *,
    step_id: str,
    contract: StepContract,
    config_root: str,
) -> dict[str, str]:
    """Set ORCHESTRATOR_STEP_DIR (step scripts resolve their own main: locally)."""
    out = {**env}
    out["ORCHESTRATOR_STEP_DIR"] = str(step_directory(step_id, contract, config_root))
    return out


def build_step_command(
    step_id: str,
    contract: StepContract,
    config_root: str,
) -> list[str]:
    """Argv for subprocess: ``run: script.sh`` (calls Python via env) or direct ``main:``."""
    if contract.run:
        if not os.path.isfile(contract.run):
            raise FileNotFoundError(f"step script not found: {contract.run}")
        return ["bash", contract.run]
    if contract.main:
        step_dir = step_directory(step_id, contract, config_root)
        main_path = resolve_main_path(step_dir, contract.main, config_root)
        if not main_path.is_file():
            raise FileNotFoundError(f"step main not found: {main_path}")
        return [sys.executable, str(main_path)]
    raise ValueError(f"step {step_id!r}: contract has neither main: nor run:")


def run_step_subprocess(
    step_id: str,
    contract: StepContract,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run step entrypoint with merged env; returns completed process."""
    from orchestrator_next.paths import config_root as _config_root
    croot = str(_config_root())
    step_env = apply_step_paths(env, step_id=step_id, contract=contract, config_root=croot)
    cmd = build_step_command(step_id, contract, croot)
    return subprocess.run(
        cmd,
        env=step_env,
        capture_output=True,
        text=True,
    )
