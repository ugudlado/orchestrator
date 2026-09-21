"""Shared pytest fixtures for orchestrator_next tests."""
from __future__ import annotations

from pathlib import Path

import pytest

#: The checkout's own vendored pack — the real workflows the engine ships for.
VENDORED_PACK = Path(__file__).resolve().parents[2] / ".orchestrator" / "workflows"


@pytest.fixture
def real_pack() -> Path:
    """The vendored pack root, skipping the test when it is not installed."""
    if not (VENDORED_PACK / "workflows").is_dir():
        pytest.skip(f"no vendored pack at {VENDORED_PACK}")
    return VENDORED_PACK
