"""
orchestrator doctor — structural health check command.

Runs seven independent checks and prints a PASS/WARN/FAIL table to stdout.
Exit codes: 0 (all pass), 1 (warnings only), 2 (at least one failure).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import namedtuple
from pathlib import Path

import yaml
import duckdb

from orchestrator_next import parser as _parser
from orchestrator_next import upsert as _upsert

CheckResult = namedtuple("CheckResult", "name status detail")

EXPECTED_TABLES = ("step_events", "tool_calls")


# ---------------------------------------------------------------------------
# Check 1: state.yaml validity
# ---------------------------------------------------------------------------

def check_state_valid() -> CheckResult:
    """Parse each ~/.workflows/*/state.yaml via parser.load_state(). FAIL if any fails."""
    failures = []
    for path in glob.glob(os.path.expanduser("~/.workflows/*/state.yaml")):
        try:
            _parser.load_state(path)
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        return CheckResult("state.yaml validity", "FAIL", "; ".join(failures))
    return CheckResult("state.yaml validity", "PASS", "all state files valid")


# ---------------------------------------------------------------------------
# Check 2: active vs archived
# ---------------------------------------------------------------------------

def check_active_vs_archive(orch_home: Path) -> CheckResult:
    """WARN if any active change_id is a substring of an archive basename."""
    active_ids = [
        os.path.basename(p.rstrip("/"))
        for p in glob.glob(os.path.expanduser("~/.workflows/*/"))
    ]
    archive_dir = orch_home / "spec" / "changes" / "archive"
    archive_names = [f.name for f in archive_dir.iterdir()] if archive_dir.is_dir() else []
    matches = []
    for cid in active_ids:
        for aname in archive_names:
            if cid in aname:
                matches.append(f"{cid} in archive/{aname}")
    if matches:
        return CheckResult("active vs archived", "WARN", "; ".join(matches))
    return CheckResult("active vs archived", "PASS", "no stale active states")


# ---------------------------------------------------------------------------
# Check 3: contract invariants
# ---------------------------------------------------------------------------

def check_contracts(orch_home: Path) -> CheckResult:
    """FAIL if any contract is missing id, inputs, or outputs."""
    failures = []
    for path in glob.glob(str(orch_home / "config" / "steps" / "*.yaml")):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            missing = [k for k in ("id", "inputs", "outputs") if k not in data]
            if missing:
                failures.append(f"{os.path.basename(path)}: missing {', '.join(missing)}")
        except Exception as exc:
            failures.append(f"{os.path.basename(path)}: {exc}")
    if failures:
        return CheckResult("contract invariants", "FAIL", "; ".join(failures))
    return CheckResult("contract invariants", "PASS", "all contracts valid")


# ---------------------------------------------------------------------------
# Check 4: inline scripts exist
# ---------------------------------------------------------------------------

def check_inline_scripts(orch_home: Path) -> CheckResult:
    """FAIL if any inline contract's run: script is missing."""
    failures = []
    for path in glob.glob(str(orch_home / "config" / "steps" / "*.yaml")):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if data.get("inline") is True:
                script = data.get("run", "")
                resolved = orch_home / script
                if not resolved.exists():
                    cid = data.get("id", os.path.basename(path))
                    failures.append(f"{cid}: missing {resolved}")
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        return CheckResult("inline scripts exist", "FAIL", "; ".join(str(f) for f in failures))
    return CheckResult("inline scripts exist", "PASS", "all inline scripts present")


# ---------------------------------------------------------------------------
# Check 5: agent files exist
# ---------------------------------------------------------------------------

def check_agent_files(orch_home: Path) -> CheckResult:
    """WARN if any contract's agent file is missing in both search locations."""
    missing = []
    for path in glob.glob(str(orch_home / "config" / "steps" / "*.yaml")):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            name = data.get("agent")
            if not name or name == "inline":
                continue
            local = orch_home / "agents" / f"{name}.md"
            global_ = Path.home() / ".claude" / "agents" / f"{name}.md"
            if not local.exists() and not global_.exists():
                cid = data.get("id", os.path.basename(path))
                missing.append(f"{name} (in {cid})")
        except Exception as exc:
            missing.append(f"{path}: {exc}")
    if missing:
        return CheckResult("agent files exist", "WARN", "; ".join(missing))
    return CheckResult("agent files exist", "PASS", "all agent files found")


# ---------------------------------------------------------------------------
# Check 6: DuckDB schema
# ---------------------------------------------------------------------------

def check_duckdb_schema(db_path: Path) -> CheckResult:
    """FAIL if step_events or tool_calls tables are missing in metrics DB."""
    conn = None
    try:
        conn = duckdb.connect(str(db_path))
        _upsert.ensure_schema(conn)
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        present = {r[0] for r in rows}
        missing = [t for t in EXPECTED_TABLES if t not in present]
        if missing:
            hint = "run `orchestrator next` once or call `ensure_schema()`"
            return CheckResult("duckdb schema", "FAIL", f"missing tables: {', '.join(missing)}; {hint}")
        return CheckResult("duckdb schema", "PASS", "schema OK")
    except Exception as exc:
        return CheckResult("duckdb schema", "FAIL", str(exc))
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Check 7: workflow plan consistency
# ---------------------------------------------------------------------------

def check_workflow_plans(orch_home: Path) -> CheckResult:
    """WARN if any active workflow plan step has no corresponding contract."""
    missing = []
    for path in glob.glob(os.path.expanduser("~/.workflows/*/state.yaml")):
        try:
            state = _parser.load_state(path)
        except Exception:
            continue
        plan = state.workflow_plan or {}
        for phase_data in plan.values():
            if not isinstance(phase_data, dict):
                continue
            for item in phase_data.get("active", []):
                # Normalize: dict -> item["id"], "step if flag" -> "step", plain str -> itself
                if isinstance(item, dict):
                    step_id = item.get("id", "")
                elif isinstance(item, str) and " if " in item:
                    step_id = item.split(" if ")[0].strip()
                else:
                    step_id = item
                if step_id and not (orch_home / "config" / "steps" / f"{step_id}.yaml").exists():
                    missing.append(f"{state.change_id}: {step_id}")
    if missing:
        return CheckResult("workflow plan consistency", "WARN", "; ".join(missing))
    return CheckResult("workflow plan consistency", "PASS", "all plan steps have contracts")


# ---------------------------------------------------------------------------
# run_all + formatting
# ---------------------------------------------------------------------------

def _format_table(results: list) -> str:
    """Render a fixed-width 3-column table: name, status, detail."""
    name_w = max(len(r.name) for r in results)
    lines = []
    for r in results:
        detail = r.detail
        if len(detail) > 100:
            detail = detail[:97] + "..."
        lines.append(f"{r.name:<{name_w}}  {r.status:<4}  {detail}")
    return "\n".join(lines)


def run_all(args) -> int:
    """Run all seven checks and return exit code 0/1/2."""
    orch_home = Path(os.environ["ORCHESTRATOR_HOME"])
    db_path = Path(os.environ.get("METRICS_DB") or str(orch_home / "metrics.duckdb"))
    results = [
        check_state_valid(),
        check_active_vs_archive(orch_home),
        check_contracts(orch_home),
        check_inline_scripts(orch_home),
        check_agent_files(orch_home),
        check_duckdb_schema(db_path),
        check_workflow_plans(orch_home),
    ]
    print(_format_table(results))
    if any(r.status == "FAIL" for r in results):
        return 2
    if any(r.status == "WARN" for r in results):
        return 1
    return 0


def _doctor_main(argv: list) -> int:
    """Entry point for `orchestrator doctor`. Validates env, then runs all checks."""
    ap = argparse.ArgumentParser(prog="orchestrator doctor")
    ap.parse_args(argv)  # --help handled here; no flags in this iteration

    if not os.environ.get("ORCHESTRATOR_HOME"):
        print("error: ORCHESTRATOR_HOME is not set", file=sys.stderr)
        return 3
    return run_all(argv)
