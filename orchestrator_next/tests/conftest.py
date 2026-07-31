"""Shared pytest fixtures for orchestrator_next tests."""
from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    """Tests need a real workflow pack. Resolve via the normal chain — a dev
    checkout's config/ if present, else the downloaded ~/.orchestrator/pack —
    and pin it so subprocess-spawning tests inherit a stable root."""
    stale = os.environ.get("ORCHESTRATOR_CONFIG")
    if stale and not (Path(stale) / "workflows").is_dir():
        del os.environ["ORCHESTRATOR_CONFIG"]  # stale export (e.g. deleted repo config/)
    if "ORCHESTRATOR_CONFIG" not in os.environ:
        from orchestrator_next.paths import ConfigRootError, config_root

        try:
            os.environ["ORCHESTRATOR_CONFIG"] = str(config_root())
        except ConfigRootError:
            pass  # pack-dependent tests will fail with the download hint
