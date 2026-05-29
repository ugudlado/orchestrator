"""T-4 (RED) / T-5 (GREEN): subprocess-driven tests for the pricing CLI.

The pricing CLI (`python3 -m orchestrator_next.pricing --agents a b c …`) is the
bulk pricer that `estimate-cost.sh` shells out to (ORC-71, Decisions D-2/D-5/D-6).
It is a pure pricer: it prices exactly the caller-supplied agents and does NOT
discover or enumerate agents.

Contract under test (design.md Component 2, AC-6/AC-7):
  - `--agents` is required and must carry a non-empty list; missing flag or empty
    list → non-zero exit, usage error on stderr, empty stdout.
  - Output is a JSON array, one object per agent, keys:
    agent, backend, model, input_usd, output_usd, cache_read_usd, cache_creation_usd.
  - An agent not in routes.yaml still produces a priced entry via the `__default__`
    pricing row (backend/model null).
  - The `effective_from <= now` filter applies (future-dated rows do not leak).
  - The `-YYYYMMDD` dated-suffix strip applies (dated model prices at its base).
  - DB absent (`METRICS_DB=/nonexistent`) → non-zero exit, empty stdout, stderr
    diagnostic, no fabricated rates.

RED phase: these fail today because pricing.py has no `main`/`__main__`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent.resolve()
_SCRIPTS_DIR = _HERE.parents[1]            # config/scripts/
_REPO_ROOT = _HERE.parents[3]              # repo root
_ROUTES_YAML = _REPO_ROOT / "scripts" / "routes.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_db(db_path: Path) -> None:
    """Create a DuckDB file with the pricing table seeded from the real
    migrations via orchestrator_next.upsert.ensure_schema."""
    scripts_dir = str(_SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import orchestrator_next.upsert as upsert_mod  # noqa: PLC0415

    db = duckdb.connect(str(db_path))
    try:
        upsert_mod.ensure_schema(db)
    finally:
        db.close()


def _run_cli(args: list[str], env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Invoke `python3 -m orchestrator_next.pricing` with the given args/env."""
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SCRIPTS_DIR)
    env["ORCHESTRATOR_HOME"] = str(_REPO_ROOT)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "orchestrator_next.pricing", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """Tmp DuckDB file seeded with the real pricing table."""
    db_path = tmp_path / "metrics.duckdb"
    _seed_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# JSON-array contract (AC-7)
# ---------------------------------------------------------------------------

def test_bulk_emits_json_array_of_n_objects(seeded_db: Path):
    """`--agents developer architect` emits a JSON array of 2 objects, each with
    the full key set."""
    result = _run_cli(
        ["--agents", "developer", "architect"],
        {"METRICS_DB": str(seeded_db)},
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}.\nstderr: {result.stderr[:500]}"
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, list), "CLI output is not a JSON array"
    assert len(payload) == 2, f"Expected 2 objects for 2 agents, got {len(payload)}"

    expected_keys = {
        "agent", "backend", "model",
        "input_usd", "output_usd", "cache_read_usd", "cache_creation_usd",
    }
    for obj in payload:
        assert set(obj.keys()) == expected_keys, (
            f"Object keys {set(obj.keys())} != expected {expected_keys}"
        )
    assert {o["agent"] for o in payload} == {"developer", "architect"}


def test_all_four_pricing_columns_present_and_non_null(seeded_db: Path):
    """For a seeded routed model, all four pricing columns are present and
    non-null (AC-7, D-5)."""
    result = _run_cli(["--agents", "discoverer"], {"METRICS_DB": str(seeded_db)})
    assert result.returncode == 0, result.stderr[:500]
    obj = json.loads(result.stdout)[0]
    # discoverer → sonnet → claude-sonnet-4-6 (seeded: 3/15/0.30/3.75)
    assert obj["input_usd"] == pytest.approx(3.00, rel=1e-9)
    assert obj["output_usd"] == pytest.approx(15.00, rel=1e-9)
    assert obj["cache_read_usd"] == pytest.approx(0.30, rel=1e-9)
    assert obj["cache_creation_usd"] is not None
    assert obj["cache_creation_usd"] == pytest.approx(3.75, rel=1e-9)


def test_single_invocation_for_n_agents(seeded_db: Path):
    """One CLI call with N agents returns one JSON array of N objects — a single
    process prices all agents (AC-7: exactly one Python process spawned)."""
    agents = ["developer", "architect", "reviewer", "discoverer"]
    result = _run_cli(["--agents", *agents], {"METRICS_DB": str(seeded_db)})
    assert result.returncode == 0, result.stderr[:500]
    payload = json.loads(result.stdout)
    assert len(payload) == len(agents)
    assert {o["agent"] for o in payload} == set(agents)


def test_agent_absent_from_routes_still_priced(seeded_db: Path):
    """An agent name not in routes.yaml still yields a priced JSON entry via the
    `__default__` pricing row — archive-observed-agent parity (AC-7)."""
    result = _run_cli(
        ["--agents", "some-archive-only-agent"],
        {"METRICS_DB": str(seeded_db)},
    )
    assert result.returncode == 0, result.stderr[:500]
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    obj = payload[0]
    assert obj["agent"] == "some-archive-only-agent"
    # Unrouted → __default__ pricing (15/75/1.50/18.75 from the seed migration).
    assert obj["input_usd"] == pytest.approx(15.00, rel=1e-9)
    assert obj["output_usd"] == pytest.approx(75.00, rel=1e-9)
    assert obj["cache_read_usd"] == pytest.approx(1.50, rel=1e-9)


# ---------------------------------------------------------------------------
# --agents required / non-empty (AC-7, D-6 — pure pricer)
# ---------------------------------------------------------------------------

def test_no_agents_flag_exits_nonzero(seeded_db: Path):
    """Invoking the CLI with no `--agents` flag exits non-zero with a usage error
    on stderr and empty stdout (D-6 — the CLI does not discover agents)."""
    result = _run_cli([], {"METRICS_DB": str(seeded_db)})
    assert result.returncode != 0, "CLI must exit non-zero with no --agents flag"
    assert result.stdout.strip() == "", f"Expected empty stdout, got: {result.stdout!r}"
    assert result.stderr.strip() != "", "Expected a usage error on stderr"


def test_empty_agents_list_exits_nonzero(seeded_db: Path):
    """Invoking the CLI with `--agents` and zero names exits non-zero with a
    usage error (AC-7, D-6)."""
    result = _run_cli(["--agents"], {"METRICS_DB": str(seeded_db)})
    assert result.returncode != 0, "CLI must exit non-zero with an empty --agents list"
    assert result.stdout.strip() == "", f"Expected empty stdout, got: {result.stdout!r}"
    assert result.stderr.strip() != "", "Expected a usage error on stderr"


# ---------------------------------------------------------------------------
# effective_from filter (AC-6, D-3)
# ---------------------------------------------------------------------------

def test_future_dated_row_not_applied(seeded_db: Path):
    """A future-dated pricing row is NOT applied before its effective_from — the
    CLI prices at `now` (AC-6, D-3)."""
    # Insert a far-future row for claude-sonnet-4-6; it must not win today.
    db = duckdb.connect(str(seeded_db))
    try:
        db.execute(
            "INSERT OR REPLACE INTO pricing "
            "(model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, "
            "is_local, effective_from) VALUES (?, ?, ?, ?, ?, FALSE, ?)",
            ["claude-sonnet-4-6", 999.0, 999.0, 999.0, 999.0, "2099-01-01T00:00:00"],
        )
    finally:
        db.close()

    result = _run_cli(["--agents", "discoverer"], {"METRICS_DB": str(seeded_db)})
    assert result.returncode == 0, result.stderr[:500]
    obj = json.loads(result.stdout)[0]
    assert obj["input_usd"] == pytest.approx(3.00, rel=1e-9), (
        f"Future-dated 999.0 row leaked into today's price: got {obj['input_usd']}"
    )


# ---------------------------------------------------------------------------
# dated-suffix strip (AC-6, D-4)
# ---------------------------------------------------------------------------

def test_dated_suffix_model_resolves_to_base_rate(seeded_db: Path, tmp_path: Path):
    """A dated-suffix model id (base seeded, dated form absent) resolves to its
    base-model rate (AC-6, D-4).

    Uses a tmp routes.yaml that routes an agent to a backend whose model_id is a
    dated variant of claude-sonnet-4-6 — the dated form is not seeded, so the
    `-YYYYMMDD` strip must kick in and price it at the base sonnet rate (3.00).
    """
    routes_dir = tmp_path / "scripts"
    routes_dir.mkdir()
    (routes_dir / "routes.yaml").write_text(
        "agents:\n"
        "  dated-agent: native_dated\n"
        "backends:\n"
        "  native_dated: claude-sonnet-4-6-20260315\n"
    )
    result = _run_cli(
        ["--agents", "dated-agent"],
        {"METRICS_DB": str(seeded_db), "ORCHESTRATOR_HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr[:500]
    obj = json.loads(result.stdout)[0]
    assert obj["input_usd"] == pytest.approx(3.00, rel=1e-9), (
        f"Dated model did not price at base sonnet rate 3.00, got {obj['input_usd']}"
    )


# ---------------------------------------------------------------------------
# DB absent — fail loud (AC-4, D-2)
# ---------------------------------------------------------------------------

def test_db_absent_exits_nonzero_no_stdout(tmp_path: Path):
    """DB absent (`METRICS_DB=/nonexistent`) → non-zero exit, empty stdout,
    stderr diagnostic, no fabricated rates (AC-4, D-2)."""
    missing = tmp_path / "does-not-exist.duckdb"
    result = _run_cli(["--agents", "developer"], {"METRICS_DB": str(missing)})
    assert result.returncode != 0, "CLI must exit non-zero when the metrics DB is absent"
    assert result.stdout.strip() == "", (
        f"Expected empty stdout when DB absent, got: {result.stdout!r}"
    )
    assert result.stderr.strip() != "", "Expected a stderr diagnostic when DB absent"
