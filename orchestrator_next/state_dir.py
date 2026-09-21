"""Where run documents live.

The driver names the state directory — `--state <dir>` on every verb, or
ORCHESTRATOR_STATE as the only fallback. The engine guesses nothing: a run
document is `<state-dir>/<run_id>.yaml`, and artifacts hang off the run's own
`repo_root` (see paths.artifacts_dir), not off this directory.

This replaces the old RunStore, which existed only to move run blobs between a
SQL store and a materialized file. With file-only state there is nothing to
materialize: the document IS the file.
"""
from __future__ import annotations

import os
from pathlib import Path


class StateDirError(RuntimeError):
    """No state directory was named."""


#: Set by `--state` before any verb runs; the env var is the fallback.
ENV_STATE_DIR = "ORCHESTRATOR_STATE"


def state_dir() -> Path:
    """The directory run documents live in. Explicit only."""
    raw = os.environ.get(ENV_STATE_DIR, "").strip()
    if not raw:
        raise StateDirError(
            "no state directory set — pass --state <dir> or set "
            f"{ENV_STATE_DIR}"
        )
    return Path(raw).expanduser()


def run_path(run_id: str) -> Path:
    """The document for ``run_id``."""
    return state_dir() / f"{run_id}.yaml"


def list_run_ids() -> list[str]:
    """Every run id the state directory holds, oldest name first."""
    try:
        root = state_dir()
    except StateDirError:
        return []
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.yaml"))
