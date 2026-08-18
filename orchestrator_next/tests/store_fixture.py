"""Test helper: point the RunStore at a throwaway SQLite home."""
from __future__ import annotations

import tempfile
from pathlib import Path

from orchestrator_next.run_store import SqliteRunStore


def install_test_store(monkeypatch) -> SqliteRunStore:
    # Resolve symlinks (macOS /var -> /private/var): record's
    # _persist_if_materialized compares a resolved state path against
    # _state_root(), so the env var must hold the resolved form.
    home = str(Path(tempfile.mkdtemp(prefix="orc-test-home-")).resolve())
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", home)
    return SqliteRunStore()
