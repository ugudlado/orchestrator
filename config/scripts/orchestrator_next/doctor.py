"""
orchestrator doctor — structural health check command.

Runs structural and graph health checks and prints a PASS/WARN/FAIL table to stdout.
Exit codes: 0 (all pass or warnings only), 2 (at least one failure).
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


def _repo_root_from_env(orch_home: Path) -> Path:
    """Resolve consumer repo root for .orchestrator override checks."""
    for key in ("ORCHESTRATOR_REPO_ROOT", "REPO_ROOT"):
        val = os.environ.get(key)
        if val:
            return Path(val).expanduser().resolve()
    return orch_home.resolve()


def _resolve_artifact(
    kind: str, name: str, repo_root: Path, orch_home: Path
) -> Path | None:
    """Repo override first, then ORCHESTRATOR_HOME/config/<kind>/<name>."""
    roots = (repo_root / ".orchestrator", orch_home / "config")
    if kind == "steps":
        step_id = name
        for root in roots:
            dir_contract = root / "steps" / step_id / "contract.yaml"
            if dir_contract.is_file():
                return dir_contract
            flat = root / "steps" / f"{step_id}.yaml"
            if flat.is_file():
                return flat
        return None
    if kind == "workflows":
        wf_name = name if name.endswith(".yaml") else f"{name}.yaml"
        for root in roots:
            path = root / "workflows" / wf_name
            if path.is_file():
                return path
        return None
    if kind == "agents":
        agent_name = name if name.endswith(".md") else f"{name}.md"
        override = repo_root / ".orchestrator" / "agents" / agent_name
        if override.is_file():
            return override
        canonical = orch_home / "agents" / agent_name
        if canonical.is_file():
            return canonical
        return None
    for root in roots:
        path = root / kind / name
        if path.exists():
            return path
    return None


def _normalize_workflow_step_ref(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("id", "")).strip()
    if isinstance(item, str) and " if " in item:
        return item.split(" if ")[0].strip()
    return str(item).strip() if item else ""


def _workflow_schema_paths(repo_root: Path, orch_home: Path) -> dict[str, Path]:
    """Schema name -> authoritative workflow path (override wins)."""
    paths: dict[str, Path] = {}
    for root in (orch_home / "config" / "workflows", repo_root / ".orchestrator" / "workflows"):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.yaml")):
            paths[path.stem] = path
    return paths


def _iter_step_contract_paths(repo_root: Path, orch_home: Path) -> dict[str, Path]:
    """Step id -> contract path (override wins)."""
    found: dict[str, Path] = {}
    for root in (orch_home / "config" / "steps", repo_root / ".orchestrator" / "steps"):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "contract.yaml").is_file():
                found[child.name] = child / "contract.yaml"
            elif child.is_file() and child.suffix == ".yaml":
                found[child.stem] = child
    return found


def _load_declared_flags(repo_root: Path, orch_home: Path) -> set[str]:
    declared: set[str] = set()
    for path in (
        repo_root / ".orchestrator" / "flags.yaml",
        orch_home / "config" / "flags.yaml",
    ):
        if not path.is_file():
            continue
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue
        for section in ("gates", "behavioral"):
            block = data.get(section) or {}
            if isinstance(block, dict):
                declared.update(block.keys())
    return declared


def _collect_symlinks(root: Path) -> list[Path]:
    links: list[Path] = []
    if not root.exists():
        return links
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            if path.is_symlink():
                links.append(path)
    return links


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
        return CheckResult("inline scripts exist", "FAIL", "; ".join(failures))
    return CheckResult("inline scripts exist", "PASS", "all inline scripts present")


# ---------------------------------------------------------------------------
# Check 5: agent files exist
# ---------------------------------------------------------------------------

def check_agent_files(repo_root: Path, orch_home: Path) -> CheckResult:
    """WARN if any contract's agent file is missing in all search locations.

    Covers both dir-form (steps/<id>/contract.yaml) and flat-form
    (steps/<id>.yaml) contracts, plus repo .orchestrator overrides, via
    _iter_step_contract_paths. Agent resolution is override-aware
    (.orchestrator/agents -> orch_home/agents) with a ~/.claude/agents global
    fallback.
    """
    missing = []
    for step_id, path in _iter_step_contract_paths(repo_root, orch_home).items():
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            name = data.get("agent")
            if not name or name == "inline":
                continue
            resolved = _resolve_artifact("agents", name, repo_root, orch_home)
            global_ = Path.home() / ".claude" / "agents" / f"{name}.md"
            if resolved is None and not global_.exists():
                missing.append(f"{name} (in {step_id})")
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
# Check 8: symlink validity
# ---------------------------------------------------------------------------

def check_symlinks(repo_root: Path, orch_home: Path) -> CheckResult:
    """WARN/FAIL when symlinks under repo_root or orch_home point at missing targets."""
    stale: list[str] = []
    for root in (repo_root, orch_home):
        for link in _collect_symlinks(root):
            if link.exists():
                continue
            try:
                target = os.readlink(link)
            except OSError as exc:
                target = f"<unreadable: {exc}>"
            stale.append(f"{link} -> {target}")
    if stale:
        return CheckResult("symlinks valid", "WARN", "; ".join(stale))
    return CheckResult("symlinks valid", "PASS", "all symlink targets exist")


# ---------------------------------------------------------------------------
# Check 9: ORCHESTRATOR_HOME vs install symlink
# ---------------------------------------------------------------------------

def check_orchestrator_home(repo_root: Path, orch_home: Path) -> CheckResult:
    """FAIL when ORCHESTRATOR_HOME env does not match ~/.config/orchestrator target."""
    del repo_root, orch_home  # reserved for future repo-specific install layouts
    current = os.environ.get("ORCHESTRATOR_HOME", "").strip()
    if not current:
        return CheckResult("ORCHESTRATOR_HOME", "FAIL", "ORCHESTRATOR_HOME is not set")
    install_link = Path.home() / ".config" / "orchestrator"
    try:
        if not install_link.exists():
            return CheckResult(
                "ORCHESTRATOR_HOME",
                "WARN",
                f"install symlink missing: {install_link}",
            )
        expected = install_link.resolve()
    except OSError as exc:
        return CheckResult(
            "ORCHESTRATOR_HOME",
            "WARN",
            f"cannot resolve install symlink {install_link}: {exc}",
        )
    current_path = Path(current).expanduser().resolve()
    if current_path != expected:
        return CheckResult(
            "ORCHESTRATOR_HOME",
            "FAIL",
            f"ORCHESTRATOR_HOME points to {current_path}, expected {expected}",
        )
    return CheckResult(
        "ORCHESTRATOR_HOME",
        "PASS",
        f"matches install symlink ({expected})",
    )


# ---------------------------------------------------------------------------
# Check 10: schema → step graph
# ---------------------------------------------------------------------------

def check_schema_step_graph(repo_root: Path, orch_home: Path) -> CheckResult:
    """FAIL when a workflow step id has no resolvable contract (override-aware)."""
    failures: list[str] = []
    for schema, wf_path in sorted(_workflow_schema_paths(repo_root, orch_home).items()):
        try:
            with open(wf_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            failures.append(f"{schema}.yaml: {exc}")
            continue
        steps = data.get("steps") or []
        if not isinstance(steps, list):
            continue
        for item in steps:
            step_id = _normalize_workflow_step_ref(item)
            if not step_id:
                continue
            if _resolve_artifact("steps", step_id, repo_root, orch_home) is None:
                failures.append(
                    f"schema graph: {schema}.yaml references {step_id} which has no contract"
                )
    if failures:
        return CheckResult("schema step graph", "FAIL", "; ".join(failures))
    return CheckResult("schema step graph", "PASS", "all workflow steps resolve")


# ---------------------------------------------------------------------------
# Check 11: contract → flag graph
# ---------------------------------------------------------------------------

def check_contract_flag_graph(repo_root: Path, orch_home: Path) -> CheckResult:
    """FAIL when flags_read names are not declared in flags.yaml."""
    declared = _load_declared_flags(repo_root, orch_home)
    failures: list[str] = []
    for step_id, path in sorted(_iter_step_contract_paths(repo_root, orch_home).items()):
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            failures.append(f"{step_id}: {exc}")
            continue
        for entry in data.get("flags_read") or []:
            if isinstance(entry, dict):
                flag_name = entry.get("name")
            elif isinstance(entry, str):
                flag_name = entry
            else:
                continue
            if not flag_name:
                continue
            if flag_name not in declared:
                failures.append(
                    f"flag graph: {flag_name} in {step_id} contract not declared in flags registry"
                )
    if failures:
        return CheckResult("contract flag graph", "FAIL", "; ".join(failures))
    return CheckResult("contract flag graph", "PASS", "all flags_read entries declared")


# ---------------------------------------------------------------------------
# Check 12: contract → template graph
# ---------------------------------------------------------------------------

def check_contract_template_graph(repo_root: Path, orch_home: Path) -> CheckResult:
    """FAIL when template_paths entries do not exist on disk."""
    failures: list[str] = []
    for step_id, path in sorted(_iter_step_contract_paths(repo_root, orch_home).items()):
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            failures.append(f"{step_id}: {exc}")
            continue
        for tpl in data.get("template_paths") or []:
            if not tpl or not isinstance(tpl, str):
                continue
            tpl_path = Path(tpl)
            resolved = tpl_path if tpl_path.is_absolute() else orch_home / tpl_path
            if not resolved.is_file():
                failures.append(
                    f"template graph: {step_id} contract missing template {tpl}"
                )
    if failures:
        return CheckResult("contract template graph", "FAIL", "; ".join(failures))
    return CheckResult("contract template graph", "PASS", "all template paths exist")


# ---------------------------------------------------------------------------
# run_all + formatting
# ---------------------------------------------------------------------------

def _format_table(results: list) -> str:
    """Render a fixed-width 3-column table: name, status, detail."""
    name_w = max(len(r.name) for r in results)
    lines = []
    for r in results:
        detail = r.detail
        if len(detail) > 120:
            detail = detail[:117] + "..."
        lines.append(f"{r.name:<{name_w}}  {r.status:<4}  {detail}")
    return "\n".join(lines)


def run_all(args) -> int:
    """Run all checks and return exit code 0 (pass/warn) or 2 (any failure)."""
    del args
    orch_home = Path(os.environ["ORCHESTRATOR_HOME"])
    repo_root = _repo_root_from_env(orch_home)
    db_path = Path(os.environ.get("METRICS_DB") or str(orch_home / "metrics.duckdb"))
    results = [
        check_state_valid(),
        check_active_vs_archive(orch_home),
        check_contracts(orch_home),
        check_inline_scripts(orch_home),
        check_agent_files(repo_root, orch_home),
        check_duckdb_schema(db_path),
        check_workflow_plans(orch_home),
        check_symlinks(repo_root, orch_home),
        check_orchestrator_home(repo_root, orch_home),
        check_schema_step_graph(repo_root, orch_home),
        check_contract_flag_graph(repo_root, orch_home),
        check_contract_template_graph(repo_root, orch_home),
    ]
    print(_format_table(results))
    if any(r.status == "FAIL" for r in results):
        return 2
    return 0


def _doctor_main(argv: list) -> int:
    """Entry point for `orchestrator doctor`. Validates env, then runs all checks."""
    ap = argparse.ArgumentParser(prog="orchestrator doctor")
    ap.parse_args(argv)  # --help handled here; no flags in this iteration

    if not os.environ.get("ORCHESTRATOR_HOME"):
        print("error: ORCHESTRATOR_HOME is not set", file=sys.stderr)
        return 3
    return run_all(argv)


if __name__ == "__main__":
    raise SystemExit(_doctor_main(sys.argv[1:]))
