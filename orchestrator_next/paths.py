"""Anchor resolution for the orchestrator engine.

One module owns "where does X live" so the read-root (config) and the
write-root (metrics) cannot silently re-collide. This module currently owns
only the **metrics write-root**; the config read-root resolver lands in a
later step.

Metrics is engine infrastructure, not config: the DuckDB store pins to the
CLI install location (where this package lives), NOT to ORCHESTRATOR_CONFIG /
ORCHESTRATOR_HOME and NOT to the operated-on repo. A single metrics store
spans every repo the CLI drives — the cross-repo dashboard depends on that.
"""
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


def metrics_db_path() -> Path:
    """Resolve the metrics DuckDB path.

    METRICS_DB env var when set (explicit override); otherwise
    <cli-root>/metrics.duckdb. Deliberately consults NO config variable —
    metrics is the engine's state, pinned to the CLI, decoupled from where
    the workflow config lives.
    """
    override = os.environ.get("METRICS_DB")
    if override:
        return Path(override)
    return _cli_root() / "metrics.duckdb"


def config_root() -> Path:
    """Resolve the workflow-config root: the directory holding workflows/,
    steps/, and agents.yaml.

    Precedence:
      1. ORCHESTRATOR_CONFIG  — points at the config/ dir directly
      2. ORCHESTRATOR_HOME/config  — legacy: var is the repo root, config/ is a subdir
      3. <cwd>/config  — default "config lives in the repo you're in"

    INVARIANT: this function owns the trailing `config` segment. Callers must
    join only what's *below* it (e.g. config_root() / "steps"), NOT
    config_root() / "config" / "steps" — that double-joins and breaks
    resolution.
    """
    explicit = os.environ.get("ORCHESTRATOR_CONFIG")
    if explicit:
        return Path(explicit)
    home = os.environ.get("ORCHESTRATOR_HOME")
    if home:
        return Path(home) / "config"
    return Path.cwd() / "config"
