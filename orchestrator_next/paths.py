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
import re
import subprocess
from pathlib import Path


def _remote_origin_slug(repo_root: str) -> str:
    """Derive a short repo name from git remote origin.

    Handles both SSH (git@github.com:org/repo.git) and HTTPS
    (https://github.com/org/repo.git) remote URLs. Falls back to the
    basename of repo_root when no remote is configured.
    """
    try:
        url = subprocess.check_output(
            ["git", "-C", repo_root, "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # Strip .git suffix, then take the last path component.
        name = re.sub(r"\.git$", "", url)
        name = name.split("/")[-1].split(":")[-1]
        if name:
            return name
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return os.path.basename(repo_root.rstrip("/"))


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


def state_dir(repo_root: str, slug: str) -> Path:
    """Canonical state directory: ~/.config/orchestrator/<repo-name>/<slug>/

    repo-name is derived from git remote origin (last path component, no .git).
    Falls back to basename of repo_root when no remote is configured.
    This is the only place that owns this convention.
    """
    repo_name = _remote_origin_slug(repo_root)
    return Path("~/.config/orchestrator").expanduser() / repo_name / slug


def state_file_path(repo_root: str, slug: str, schema: str, timestamp: str) -> Path:
    """Return the path for a new state file: <state_dir>/<timestamp>_<schema>_state.yaml

    timestamp should be UTC in %Y%m%dT%H%M%S format. Callers generate it so
    this function stays pure and testable without mocking time.
    """
    return state_dir(repo_root, slug) / f"{timestamp}_{schema}_state.yaml"


def latest_state_file(repo_root: str, slug: str) -> Path | None:
    """Return the most recent *_state.yaml in the slug dir, or None if empty.

    Files sort lexicographically by timestamp prefix (%Y%m%dT%H%M%S), so the
    last entry is the most recent run regardless of schema.
    """
    d = state_dir(repo_root, slug)
    if not d.is_dir():
        return None
    files = sorted(d.glob("*_state.yaml"))
    return files[-1] if files else None


def existing_schema_state_file(repo_root: str, slug: str, schema: str) -> Path | None:
    """Return the existing state file for this schema if one already exists, else None.

    Used for idempotency: if a *_<schema>_state.yaml already exists in the slug
    dir, seeding is skipped and this path is returned to the caller.
    """
    d = state_dir(repo_root, slug)
    if not d.is_dir():
        return None
    matches = sorted(d.glob(f"*_{schema}_state.yaml"))
    return matches[-1] if matches else None


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
