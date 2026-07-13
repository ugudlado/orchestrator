"""Shared pytest fixtures for orchestrator_next tests."""
from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    """Default bundled config when tests don't override via ORCHESTRATOR_HOME."""
    if "ORCHESTRATOR_CONFIG" not in os.environ and "ORCHESTRATOR_HOME" not in os.environ:
        os.environ["ORCHESTRATOR_CONFIG"] = str(
            Path(__file__).resolve().parents[2] / "config"
        )
