"""Test helper: point the engine at a throwaway state directory."""
from __future__ import annotations

import tempfile
from pathlib import Path

from orchestrator_next.state_dir import ENV_STATE_DIR


def install_test_store(monkeypatch) -> Path:
    """Give this test its own `--state` directory, as a driver would pass."""
    # Resolve symlinks (macOS /var -> /private/var) so paths compare equal.
    root = Path(tempfile.mkdtemp(prefix="orc-test-state-")).resolve()
    monkeypatch.setenv(ENV_STATE_DIR, str(root))
    return root
