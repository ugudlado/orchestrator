"""Anchor resolution for the orchestrator engine — paths for config and state."""
from __future__ import annotations

import os
from pathlib import Path


def _cli_root() -> Path:
    """Repo root where this package is installed.

    orchestrator_next/paths.py → .parent is the package dir, .parent.parent is
    the repo root. resolve() follows a symlinked install on $PATH to the real
    checkout (same mechanism as bin/orchestrator's realpath).
    """
    return Path(__file__).resolve().parent.parent


class ConfigRootError(RuntimeError):
    """Raised when no config root is set — resolution is explicit-only."""


def bundled_config_root() -> Path:
    """The config/ dir shipped alongside the code — package data in a wheel
    install (orchestrator_next/config), the repo's config/ in a dev checkout.

    This is what `orchestrator config-path` prints; it is NOT consulted
    implicitly — export ORCHESTRATOR_CONFIG to use it.
    """
    bundled = Path(__file__).resolve().parent / "config"
    if bundled.is_dir():
        return bundled
    return _cli_root() / "config"


def config_root() -> Path:
    """Resolve the workflow-config root: the directory holding workflows/,
    steps/, and agents.yaml.

    Resolution order (first hit wins, no layering/merging):
      1. ORCHESTRATOR_CONFIG  — points at the config/ dir directly
      2. <repo_root>/.orchestrator/config/ — vendored pack, if it has workflows/
         (repo_root from ORCHESTRATOR_REPO_ROOT or REPO_ROOT env)

    Neither hit → ConfigRootError.

    INVARIANT: this function owns the trailing `config` segment. Callers must
    join only what's *below* it (e.g. config_root() / "steps"), NOT
    config_root() / "config" / "steps" — that double-joins and breaks
    resolution.
    """
    explicit = os.environ.get("ORCHESTRATOR_CONFIG")
    if explicit:
        return Path(explicit)
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    if repo_root:
        vendored = Path(repo_root) / ".orchestrator" / "config"
        if (vendored / "workflows").is_dir():
            return vendored
    raise ConfigRootError(
        "config root not set — export ORCHESTRATOR_CONFIG=$(orchestrator config-path) "
        "for the bundled config, or point it at a config checkout's config/ dir"
    )
