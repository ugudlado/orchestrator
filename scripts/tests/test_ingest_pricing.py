"""T-11 RED: tests for scripts/ingest-pricing.py

Scenarios:
  (a) Happy path — insert row, exit 0, stdout = "inserted foo @ 2026-06-01T00:00:00"
  (b) Duplicate (model_id, effective_from) — exit non-zero, stderr mentions "duplicate"
  (c) Validation error: --input-usd -1.0 — exit non-zero before any DB write
  (d) --help contains worked example — exit 0, stdout contains --model and --effective-from
  (e) Import resilience — --help runs without ImportError/ModuleNotFoundError on stderr,
      both with ORCHESTRATOR_HOME set and with it unset (F-5)

All tests invoke the script via subprocess (never import it) to test real CLI behaviour.
The seeded_db fixture closes the connection before yielding the path so DuckDB's
exclusive write lock is not held when the subprocess opens the same file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent  # .../pricing-table-in-duckdb
_SCRIPTS_DIR = _REPO_ROOT / "config" / "scripts"
_SCRIPT = _REPO_ROOT / "scripts" / "ingest-pricing.py"

# Add config/scripts to sys.path so we can import orchestrator_next.upsert in
# the fixture (same pattern as test_migrations.py).
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_db(tmp_path):
    """Return path to a fresh DuckDB file with full schema (pricing table seeded).

    The connection is closed before the path is yielded so the subprocess that
    invokes ingest-pricing.py can acquire the write lock without conflict.
    """
    db_path = tmp_path / "test.duckdb"
    db = duckdb.connect(str(db_path))
    ensure_schema(db)
    db.close()
    yield str(db_path)


def _run(args, env_overrides=None, **kwargs):
    """Run ingest-pricing.py with the given extra args.

    env_overrides: dict of {key: value} to add/replace in environment, or
                   {key: None} to remove a key from the environment.
    """
    env = dict(os.environ)
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Scenario (a): Happy path
# ---------------------------------------------------------------------------


def test_happy_path_inserts_row(seeded_db):
    """Run script with valid args → exit 0, row visible in DB, stdout correct."""
    result = _run(
        [
            "--model", "foo",
            "--input-usd", "1.0",
            "--output-usd", "2.0",
            "--cache-read-usd", "0.1",
            "--cache-creation-usd", "1.25",
            "--effective-from", "2026-06-01T00:00:00",
            "--db", seeded_db,
        ]
    )
    assert result.returncode == 0, f"Expected exit 0; stderr={result.stderr!r}"
    assert result.stdout.strip() == "inserted foo @ 2026-06-01T00:00:00"

    # Verify the row is actually in the DB.
    db = duckdb.connect(seeded_db)
    row = db.execute(
        "SELECT model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd "
        "FROM pricing WHERE model_id = 'foo' AND effective_from = '2026-06-01T00:00:00'"
    ).fetchone()
    db.close()
    assert row is not None, "Row not found in pricing table after insert"
    assert row[0] == "foo"
    assert abs(row[1] - 1.0) < 1e-9
    assert abs(row[2] - 2.0) < 1e-9
    assert abs(row[3] - 0.1) < 1e-9
    assert abs(row[4] - 1.25) < 1e-9


# ---------------------------------------------------------------------------
# Scenario (b): Duplicate
# ---------------------------------------------------------------------------


def test_duplicate_exits_nonzero_with_duplicate_in_stderr(seeded_db):
    """Running the same insert twice → second invocation exits non-zero."""
    args = [
        "--model", "dup-model",
        "--input-usd", "1.0",
        "--output-usd", "2.0",
        "--cache-read-usd", "0.1",
        "--effective-from", "2026-06-01T00:00:00",
        "--db", seeded_db,
    ]
    first = _run(args)
    assert first.returncode == 0, f"First insert failed; stderr={first.stderr!r}"

    second = _run(args)
    assert second.returncode != 0, "Expected non-zero exit on duplicate insert"
    assert "duplicate" in second.stderr.lower(), (
        f"Expected 'duplicate' in stderr; got: {second.stderr!r}"
    )

    # The original row must be unchanged.
    db = duckdb.connect(seeded_db)
    count = db.execute(
        "SELECT COUNT(*) FROM pricing WHERE model_id = 'dup-model'"
    ).fetchone()[0]
    db.close()
    assert count == 1, f"Expected exactly 1 row; found {count}"


# ---------------------------------------------------------------------------
# Scenario (c): Validation error — negative rate rejected before any DB write
# ---------------------------------------------------------------------------


def test_negative_rate_rejected_before_db_write(seeded_db):
    """--input-usd -1.0 → exits non-zero, DB row count unchanged."""
    # Count rows before the bad invocation.
    db = duckdb.connect(seeded_db)
    before = db.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    db.close()

    result = _run(
        [
            "--model", "bad-model",
            "--input-usd", "-1.0",
            "--output-usd", "2.0",
            "--cache-read-usd", "0.1",
            "--effective-from", "2026-06-01T00:00:00",
            "--db", seeded_db,
        ]
    )
    assert result.returncode != 0, "Expected non-zero exit on negative rate"

    # Row count must be unchanged.
    db = duckdb.connect(seeded_db)
    after = db.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    db.close()
    assert after == before, (
        f"Expected row count unchanged ({before}); got {after} — DB was written before validation"
    )


# ---------------------------------------------------------------------------
# Scenario (d): --help contains worked example
# ---------------------------------------------------------------------------


def test_help_contains_worked_example():
    """--help → exit 0, stdout contains --model and --effective-from."""
    result = _run(["--help"])
    assert result.returncode == 0, f"Expected exit 0 for --help; stderr={result.stderr!r}"
    assert "--model" in result.stdout, "--model not found in --help output"
    assert "--effective-from" in result.stdout, "--effective-from not found in --help output"


# ---------------------------------------------------------------------------
# Scenario (e): Import resilience — both with and without ORCHESTRATOR_HOME
# ---------------------------------------------------------------------------


def test_import_resilience_with_orchestrator_home_set():
    """--help exits 0 with no ImportError on stderr when ORCHESTRATOR_HOME is set."""
    result = _run(
        ["--help"],
        env_overrides={"ORCHESTRATOR_HOME": str(_REPO_ROOT)},
    )
    assert result.returncode == 0, (
        f"Expected exit 0 with ORCHESTRATOR_HOME set; stderr={result.stderr!r}"
    )
    stderr_lower = result.stderr.lower()
    assert "importerror" not in stderr_lower, (
        f"ImportError found in stderr: {result.stderr!r}"
    )
    assert "modulenotfounderror" not in stderr_lower, (
        f"ModuleNotFoundError found in stderr: {result.stderr!r}"
    )


def test_import_resilience_without_orchestrator_home():
    """--help exits 0 with no ImportError on stderr when ORCHESTRATOR_HOME is unset."""
    result = _run(
        ["--help"],
        env_overrides={"ORCHESTRATOR_HOME": None},
    )
    assert result.returncode == 0, (
        f"Expected exit 0 with ORCHESTRATOR_HOME unset; stderr={result.stderr!r}"
    )
    stderr_lower = result.stderr.lower()
    assert "importerror" not in stderr_lower, (
        f"ImportError found in stderr: {result.stderr!r}"
    )
    assert "modulenotfounderror" not in stderr_lower, (
        f"ModuleNotFoundError found in stderr: {result.stderr!r}"
    )
