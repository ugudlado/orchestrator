"""
T-9 (RED) / T-10 (GREEN): subprocess-driven tests for estimate-cost.sh.

Four scenarios:
  (a) Baseline fixture: the fixture at fixtures/estimate_cost_before.txt was
      captured with /opt/homebrew/bin/bash (GNU bash 5.3.3) against a minimal
      state_dir with no archive history (cold-start). This test validates the
      fixture parses as valid YAML — it does NOT re-run the old script.

  (b) Post-rewrite parity: after T-10, running the rewrite with the same
      minimal state_dir produces output whose YAML structure matches the
      baseline fixture — same agents, same cold-start estimate block.

  (c) DB-absent fail-loud (ORC-71, Decision D-2): setting
      METRICS_DB=/nonexistent/path the pricing CLI fails loud — no fabricated
      '15.00 75.00 1.50' rates appear anywhere. estimate-cost.sh propagates the
      failure: it exits non-zero, or its route_preview surfaces an unavailable
      state with no per-agent pricing block. The old hardcoded fallback is the
      bug being removed.

  (d) Bash 3.2 regression guard (KEY TEST for F-4 from review-specify.md):
      invoking the script via /bin/bash (macOS default bash 3.2) must exit 0
      with no 'declare -A', 'bad substitution', or 'associative arrays' errors
      in stderr, and must emit a 'route_preview:' block on stdout.

Design notes:
- REPO_ROOT, ROUTES_FILE, PRICING_FILE are set so the script resolves the
  repo's own routes.yaml (not $HOME/.config/orchestrator).
- ARCHIVE_GLOB is overridden to point at a nonexistent path (cold-start).
- METRICS_DB is set to a tmp DuckDB file for scenario (b), or
  /nonexistent/path for scenario (c).
- generated_at timestamps are stripped before YAML comparison.
- On macOS /bin/bash is always 3.2.x; on Linux BASH_COMPAT=32 is used if
  available, otherwise the test is gracefully skipped.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent.resolve()
# _HERE = .../config/scripts/orchestrator_next/tests
# parents[0] = .../config/scripts/orchestrator_next
# parents[1] = .../config/scripts
# parents[2] = .../config
# parents[3] = repo root
_SCRIPTS_DIR = _HERE.parents[1]           # config/scripts/
_REPO_ROOT = _HERE.parents[3]             # repo root
_ESTIMATE_COST_SH = _SCRIPTS_DIR / "estimate-cost.sh"
_ROUTES_YAML = _REPO_ROOT / "config" / "agents.yaml"  # ORC-105: merged from scripts/routes.yaml
_FIXTURE = _HERE / "fixtures" / "estimate_cost_before.txt"


# ---------------------------------------------------------------------------
# Module-level helpers (defined first — used in @pytest.mark.skipif decorator)
# ---------------------------------------------------------------------------

def _find_modern_bash() -> str:
    """Return the path to a bash 4+ interpreter.

    On macOS, /bin/bash is 3.2 so we check Homebrew first. On Linux the
    system bash is typically 5+.
    """
    candidates = [
        "/opt/homebrew/bin/bash",   # Homebrew bash 5 on macOS arm64
        "/usr/local/bin/bash",       # Homebrew bash 5 on macOS x86
        "bash",                      # system bash on Linux (typically 5+)
    ]
    for candidate in candidates:
        try:
            r = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                m = re.search(r"version (\d+)\.", r.stdout)
                if m and int(m.group(1)) >= 4:
                    return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "bash"  # Linux fallback


def _bash32_available() -> bool:
    """Return True if a bash 3.2 environment is available for testing."""
    if platform.system() == "Darwin":
        return Path("/bin/bash").exists()
    # Linux: try BASH_COMPAT=32 with the system bash
    try:
        r = subprocess.run(
            ["bash", "-c", "BASH_COMPAT=32; echo ok"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0 and "ok" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_bash32() -> tuple[str, dict[str, str]]:
    """Return (bash_path, extra_env) for invoking bash in 3.2-compatible mode."""
    if platform.system() == "Darwin" and Path("/bin/bash").exists():
        return "/bin/bash", {}
    # Linux fallback: set BASH_COMPAT=32 in the child environment
    return "bash", {"BASH_COMPAT": "32"}


# ---------------------------------------------------------------------------
# Other helpers
# ---------------------------------------------------------------------------

def _seed_db(db_path: Path) -> None:
    """Create a DuckDB file at db_path with the pricing table seeded from the
    real migrations. Uses orchestrator_next.upsert.ensure_schema so the data
    matches the production seed migration exactly.

    orchestrator_next is a package under config/scripts/. We add config/scripts/
    to sys.path so the import resolves regardless of where pytest is invoked.
    """
    scripts_dir = str(_SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import orchestrator_next.upsert as upsert_mod  # noqa: PLC0415

    db = duckdb.connect(str(db_path))
    try:
        upsert_mod.ensure_schema(db)
    finally:
        db.close()


def _base_env(state_dir: Path, db_path: str | None = None) -> dict[str, str]:
    """Return an env dict for running estimate-cost.sh in a controlled way.

    Overrides:
    - REPO_ROOT / ORCHESTRATOR_HOME → repo root (so routes.yaml is found)
    - ROUTES_FILE → repo's own scripts/routes.yaml
    - ARCHIVE_GLOB → nonexistent path (forces cold-start / no_history)
    - METRICS_DB → db_path if given
    """
    env = os.environ.copy()
    env["REPO_ROOT"] = str(_REPO_ROOT)
    env["ORCHESTRATOR_HOME"] = str(_REPO_ROOT)
    env["ROUTES_FILE"] = str(_ROUTES_YAML)
    env["ARCHIVE_GLOB"] = str(state_dir / "nonexistent-archive" / "*/state.yaml")
    if db_path is not None:
        env["METRICS_DB"] = db_path
    return env


def _normalise(text: str) -> str:
    """Strip generated_at lines (timestamps vary) and comment lines for comparison."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("generated_at:"):
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    """Minimal state_dir with state.yaml (schema: feature, no tasks.md)."""
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "state.yaml").write_text("schema: feature\nflags:\n  tdd_required: false\n")
    return sd


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """Tmp DuckDB file seeded with the real pricing table via ensure_schema."""
    db_path = tmp_path / "orchestrator.duckdb"
    _seed_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Scenario (a): Baseline fixture is valid YAML with expected shape
# ---------------------------------------------------------------------------

def test_baseline_fixture_is_valid_yaml():
    """(a) The baseline fixture parses as valid YAML with a route_preview key.

    Informational guard — ensures the fixture itself is well-formed. Does NOT
    run the old script. Regeneration instructions are in the fixture file header.
    """
    assert _FIXTURE.exists(), f"Fixture missing: {_FIXTURE}"
    content = _FIXTURE.read_text()
    normalised = _normalise(content)
    doc = yaml.safe_load(normalised)
    assert isinstance(doc, dict), "Fixture did not parse as a YAML dict"
    assert "route_preview" in doc, "Fixture missing 'route_preview' key"
    rp = doc["route_preview"]
    assert rp.get("estimate") is None, "Fixture should show cold-start (estimate: null)"
    assert rp.get("estimate_reason") == "no_history", (
        "Fixture cold-start reason must be 'no_history'"
    )


# ---------------------------------------------------------------------------
# Scenario (b): Post-rewrite parity with baseline fixture shape
# ---------------------------------------------------------------------------

def test_rewrite_output_matches_baseline_shape(state_dir: Path, seeded_db: Path):
    """(b) Rewritten script produces YAML whose route_preview structure matches
    the baseline fixture: same agents, schema=feature, cold-start estimate block.

    The test is primarily a regression guard post-rewrite (GREEN phase).
    """
    bash = _find_modern_bash()
    env = _base_env(state_dir, db_path=str(seeded_db))

    result = subprocess.run(
        [bash, str(_ESTIMATE_COST_SH), str(state_dir)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"estimate-cost.sh exited {result.returncode}.\nstderr: {result.stderr[:500]}"
    )

    stdout_norm = _normalise(result.stdout)
    actual_doc = yaml.safe_load(stdout_norm)
    assert actual_doc is not None, "Script produced no parseable YAML output"
    assert "route_preview" in actual_doc, (
        f"Output missing 'route_preview' key. Got:\n{result.stdout[:300]}"
    )

    # Parse fixture for expected shape
    fixture_norm = _normalise(_FIXTURE.read_text())
    expected_doc = yaml.safe_load(fixture_norm)

    actual_rp = actual_doc["route_preview"]
    expected_rp = expected_doc["route_preview"]

    assert actual_rp.get("schema") == expected_rp.get("schema"), (
        f"schema mismatch: {actual_rp.get('schema')!r} != {expected_rp.get('schema')!r}"
    )
    assert actual_rp.get("estimate") is None, (
        "Expected cold-start (no archive) — estimate must be null"
    )
    assert actual_rp.get("estimate_reason") == "no_history", (
        f"estimate_reason: {actual_rp.get('estimate_reason')!r}"
    )

    # Agent names must match routes.yaml (order-independent; fixture may lag renames)
    actual_agents = {a["agent"] for a in actual_rp.get("agents", [])}
    routes_doc = yaml.safe_load(_ROUTES_YAML.read_text()) or {}
    expected_agents = set((routes_doc.get("agents") or {}).keys())
    assert actual_agents == expected_agents, (
        f"Agent set mismatch.\nActual:   {sorted(actual_agents)}\n"
        f"Expected: {sorted(expected_agents)}"
    )

    # Spot-check: discoverer → sonnet tier (3.00 input / 15.00 output in seed DB)
    sonnet_list = [a for a in actual_rp["agents"] if a["agent"] == "discoverer"]
    assert len(sonnet_list) == 1, "Expected exactly one 'discoverer' agent entry"
    pricing = sonnet_list[0].get("pricing", {})
    assert float(pricing.get("input", 0)) == pytest.approx(3.00, rel=1e-6), (
        f"discoverer input pricing: expected 3.00, got {pricing.get('input')}"
    )
    assert float(pricing.get("output", 0)) == pytest.approx(15.00, rel=1e-6), (
        f"discoverer output pricing: expected 15.00, got {pricing.get('output')}"
    )


# ---------------------------------------------------------------------------
# Scenario (c): DB-absent fail-loud (ORC-71, Decision D-2)
# ---------------------------------------------------------------------------

def test_db_absent_fails_loud_no_fabricated_rates(state_dir: Path):
    """(c) METRICS_DB=/nonexistent/path → fail loud, no fabricated rates.

    ORC-71 Decision D-2 removed the hardcoded '15.00 75.00 1.50' fallback. When
    the metrics DB is absent the pricing CLI fails loud (non-zero exit, stderr
    diagnostic, no stdout) and estimate-cost.sh propagates the failure. This
    test asserts the fail-loud behavior:
      - no fabricated '15.00 75.00 1.50' rates appear anywhere in the output, AND
      - the estimator surfaces the failure: it either exits non-zero, or its
        route_preview carries no per-agent pricing block.

    RED phase: this FAILS on the current script (it still substitutes default
    rates and exits 0). GREEN after T-8 rewires the script to the CLI.
    """
    bash = _find_modern_bash()
    env = _base_env(state_dir, db_path="/nonexistent/path/orchestrator.duckdb")

    result = subprocess.run(
        [bash, str(_ESTIMATE_COST_SH), str(state_dir)],
        capture_output=True,
        text=True,
        env=env,
    )

    # The removed hardcoded fallback was exactly "15.00 75.00 1.50" — that
    # literal must not appear in stdout or stderr after D-2.
    combined = result.stdout + result.stderr
    assert "15.00 75.00 1.50" not in combined, (
        "Fabricated DB-absent fallback rates '15.00 75.00 1.50' still emitted — "
        "D-2 removal not applied."
    )

    # The estimator must surface the failure rather than guess. Either a
    # non-zero exit, or a route_preview with no per-agent pricing block.
    if result.returncode == 0:
        stdout_norm = _normalise(result.stdout)
        doc = yaml.safe_load(stdout_norm) if stdout_norm else None
        agents = (doc or {}).get("route_preview", {}).get("agents", []) or []
        priced = [a for a in agents if a.get("pricing") not in (None, {})]
        assert not priced, (
            "Script exited 0 with per-agent pricing blocks despite an absent DB — "
            "the estimator must fail loud, not fabricate rates.\n"
            f"stdout: {result.stdout[:300]}"
        )


# ---------------------------------------------------------------------------
# Scenario (d): Bash 3.2 regression guard — KEY TEST for F-4
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _bash32_available(),
    reason="No bash 3.2 / BASH_COMPAT=32 environment available on this host",
)
def test_bash32_no_declare_A_error(state_dir: Path, seeded_db: Path):
    """(d) Invoking estimate-cost.sh via /bin/bash (macOS bash 3.2) must succeed.

    This is the bash 3.2 regression guard closing F-4 from review-specify.md.
    Before T-10 the script fails at line 63 with 'declare: -A: invalid option'.
    After T-10 all declare -A / mapfile / readarray / ${!arr[@]} constructs are
    removed, so /bin/bash 3.2 must parse and run the script cleanly.

    RED phase: this test FAILS on the current script (expected — declare -A error).
    GREEN phase: this test PASSES after the rewrite.
    """
    bash32, extra_env = _get_bash32()
    env = _base_env(state_dir, db_path=str(seeded_db))
    env.update(extra_env)

    result = subprocess.run(
        [bash32, str(_ESTIMATE_COST_SH), str(state_dir)],
        capture_output=True,
        text=True,
        env=env,
    )

    bash32_error_patterns = [
        r"declare: -A",
        r"bad substitution",
        r"associative array",
        r"mapfile",
        r"readarray",
    ]
    for pattern in bash32_error_patterns:
        assert not re.search(pattern, result.stderr, re.IGNORECASE), (
            f"Bash 3.2 incompatible construct detected — pattern '{pattern}' in stderr:\n"
            f"{result.stderr[:500]}"
        )

    assert result.returncode == 0, (
        f"estimate-cost.sh must exit 0 under bash 3.2.\n"
        f"Exit: {result.returncode}\nstderr: {result.stderr[:500]}"
    )

    assert "route_preview:" in result.stdout, (
        f"Script did not produce 'route_preview:' output under bash 3.2.\n"
        f"stdout: {result.stdout[:300]}"
    )
