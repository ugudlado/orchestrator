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

from orchestrator_next import parser as _parser

CheckResult = namedtuple("CheckResult", "name status detail")


def _repo_root_from_env(orch_home: Path) -> Path:
    """Resolve consumer repo root for .orchestrator override checks."""
    for key in ("ORCHESTRATOR_REPO_ROOT", "REPO_ROOT"):
        val = os.environ.get(key)
        if val:
            return Path(val).expanduser().resolve()
    return orch_home.resolve()


def _normalize_workflow_step_ref(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("id", "")).strip()
    if isinstance(item, str) and " if " in item:
        return item.split(" if ")[0].strip()
    return str(item).strip() if item else ""


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

def check_config_root(config_root: Path) -> CheckResult:
    """FAIL when the resolved config root is missing or lacks the required layout.

    The config root (ORCHESTRATOR_CONFIG, else ORCHESTRATOR_HOME/config —
    see paths.config_root; explicit, no cwd fallback) must exist and hold workflows/,
    steps/, and agents.yaml. This is the prerequisite for every config-content
    check below: if the root is wrong, those checks have nothing to validate.
    """
    if not config_root.is_dir():
        return CheckResult(
            "config root", "FAIL", f"config root does not exist: {config_root}"
        )
    missing = [
        name
        for name, is_dir in (("workflows", True), ("steps", True), ("models.yaml", False))
        if not ((config_root / name).is_dir() if is_dir else (config_root / name).is_file())
    ]
    if missing:
        return CheckResult(
            "config root",
            "FAIL",
            f"{config_root} missing: {', '.join(missing)}",
        )
    return CheckResult("config root", "PASS", f"valid config root: {config_root}")


# ---------------------------------------------------------------------------
# Config-folder validation (the 4 portability rules), anchored on config_root().
# These honor ORCHESTRATOR_CONFIG; they do NOT read .orchestrator/ overrides.
# ---------------------------------------------------------------------------

def _iter_config_contracts(config_root: Path) -> dict[str, Path]:
    """Step id -> contract path under <config_root>/steps/ (dir or flat form)."""
    found: dict[str, Path] = {}
    steps_root = config_root / "steps"
    if not steps_root.is_dir():
        return found
    for child in sorted(steps_root.iterdir()):
        if child.is_dir() and (child / "contract.yaml").is_file():
            found[child.name] = child / "contract.yaml"
        elif child.is_file() and child.suffix == ".yaml":
            found[child.stem] = child
    return found


def check_workflow_steps_resolve(config_root: Path) -> CheckResult:
    """RULE 1: every step referenced by a workflow has a contract under steps/."""
    contracts = set(_iter_config_contracts(config_root))
    wf_root = config_root / "workflows"
    failures: list[str] = []
    if wf_root.is_dir():
        for wf_path in sorted(wf_root.glob("*.yaml")):
            try:
                data = yaml.safe_load(wf_path.read_text()) or {}
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{wf_path.name}: {exc}")
                continue
            for item in data.get("steps") or []:
                step_id = _normalize_workflow_step_ref(item)
                if step_id and step_id not in contracts:
                    failures.append(f"{wf_path.stem}.yaml → {step_id} (no contract)")
    if failures:
        return CheckResult("workflow steps resolve", "FAIL", "; ".join(failures))
    return CheckResult("workflow steps resolve", "PASS", "all workflow steps have contracts")


def check_step_dispatch_kind(config_root: Path) -> CheckResult:
    """RULE 2: every step contract is dispatchable — declares agent: (spawn)
    or run: (script). Script steps point run: at a script.sh that execs their
    own payload (e.g. a python file)."""
    failures: list[str] = []
    for step_id, path in _iter_config_contracts(config_root).items():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{step_id}: {exc}")
            continue
        if not (data.get("model") or data.get("run")):
            failures.append(f"{step_id}: no model:/run:")
    if failures:
        return CheckResult("step dispatch kind", "FAIL", "; ".join(failures))
    return CheckResult("step dispatch kind", "PASS", "all steps are agent- or script-driven")


def check_subprocesses_available(config_root: Path) -> CheckResult:
    """RULE 3 (WARN): every distinct subprocess: in models.yaml is on PATH.

    WARN, not FAIL: a config is valid even if a backend (e.g. cursor) is not
    installed on this machine — that's machine state, not config integrity.
    """
    import shutil

    models_yaml = config_root / "models.yaml"
    if not models_yaml.is_file():
        return CheckResult("subprocesses available", "WARN", "models.yaml not found")
    try:
        data = yaml.safe_load(models_yaml.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        return CheckResult("subprocesses available", "WARN", f"models.yaml parse: {exc}")
    subs = {
        entry.get("subprocess")
        for entry in (data.get("models") or {}).values()
        if isinstance(entry, dict) and entry.get("subprocess")
    }
    missing = sorted(s for s in subs if not shutil.which(s))
    if missing:
        return CheckResult(
            "subprocesses available", "WARN", f"not on PATH: {', '.join(missing)}"
        )
    return CheckResult(
        "subprocesses available", "PASS", f"all {len(subs)} subprocess backends on PATH"
    )


# ---------------------------------------------------------------------------
# Check 11: contract → template graph
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
    from orchestrator_next.paths import ConfigRootError, config_root as _config_root

    try:
        config_root = _config_root()
    except ConfigRootError as exc:
        print(_format_table([CheckResult("config root", "FAIL", str(exc))]))
        return 2
    orch_home = config_root.parent
    repo_root = _repo_root_from_env(orch_home)
    results = [
        # Config-folder validation (the 4 portability rules) — anchored on config_root().
        check_config_root(config_root),
        check_workflow_steps_resolve(config_root),  # rule 1
        check_step_dispatch_kind(config_root),      # rule 2
        check_subprocesses_available(config_root),  # rule 3 (WARN)
        # Engine/state health (legacy anchor: orch_home = config_root.parent).
        check_state_valid(),
        check_active_vs_archive(orch_home),
        check_inline_scripts(orch_home),
        check_workflow_plans(orch_home),
        check_symlinks(repo_root, orch_home),
        check_contract_template_graph(repo_root, orch_home),
    ]
    print(_format_table(results))
    if any(r.status == "FAIL" for r in results):
        return 2
    return 0


def _doctor_main(argv: list) -> int:
    """Entry point for `orchestrator doctor`. Runs all checks.

    No ORCHESTRATOR_HOME guard: the config root resolves via paths.config_root
    (ORCHESTRATOR_CONFIG → ORCHESTRATOR_HOME/config; explicit, no cwd fallback).
    A wrong, unset, or missing config root surfaces as the `config root` FAIL
    check, not a crash.
    """
    ap = argparse.ArgumentParser(prog="orchestrator doctor")
    ap.parse_args(argv)  # --help handled here; no flags in this iteration
    return run_all(argv)


if __name__ == "__main__":
    raise SystemExit(_doctor_main(sys.argv[1:]))
