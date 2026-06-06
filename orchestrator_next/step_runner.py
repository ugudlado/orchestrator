"""Resolve and execute workflow step entrypoints (bash run:)."""
from __future__ import annotations

import os
from pathlib import Path

from orchestrator_next.parser import ScriptStepContract


def step_directory(step_id: str, contract: ScriptStepContract, config_root: str) -> Path:
    """Directory for <config_root>/steps/<step_id>/ (authoritative, not dirname of _runner).

    `config_root` is the config/ dir itself (see paths.config_root) — this
    function joins `steps/<id>` directly, NOT `config/steps/<id>`.
    """
    if contract.run and os.path.isabs(contract.run):
        run_dir = Path(contract.run).parent
        if run_dir.name != "_runner":
            return run_dir
    return Path(config_root) / "steps" / step_id


def apply_step_paths(
    env: dict[str, str],
    *,
    step_id: str,
    contract: ScriptStepContract,
    config_root: str,
) -> dict[str, str]:
    """Set ORCHESTRATOR_STEP_DIR (step scripts resolve their own payload locally)."""
    out = {**env}
    out["ORCHESTRATOR_STEP_DIR"] = str(step_directory(step_id, contract, config_root))
    return out


def build_step_command(
    step_id: str,
    contract: ScriptStepContract,
    config_root: str,
) -> list[str]:
    """Argv for subprocess: ``run: script.sh`` (the script calls Python via env)."""
    if not os.path.isfile(contract.run):
        raise FileNotFoundError(f"step script not found: {contract.run}")
    return ["bash", contract.run]
