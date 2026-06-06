"""
Validate a workflow schema and its step contracts (ORC-*).

Checks:
  1. Schema file exists under config/workflows/<name>.yaml
  2. Every step has a contract (contract.yaml or flat <step>.yaml)
  3. Every contract declares either agent: or run:
  4. generate_plan succeeds on a synthetic state (skipped for operator schemas)

Public API: validate_workflow(schema_name: str, repo_root: str) -> None
Raises SystemExit(1) on any failure, prints diagnostics to stderr.

Entry point: orchestrator validate-workflow <schema-name>
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Schemas that have no standard seed shape — skip the generate_plan smoke.
_SKIP_EXPAND = {"complete"}


def _config_root(repo_root: str) -> Path:
    return Path(repo_root) / "config"


def _steps_dir(repo_root: str) -> Path:
    return _config_root(repo_root) / "steps"


def _load_schema(schema_name: str, repo_root: str) -> dict[str, Any]:
    path = _config_root(repo_root) / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        print(f"ERROR: workflow not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _step_ids(schema: dict[str, Any]) -> list[str]:
    """Extract step IDs from a flat or phased schema."""
    steps = schema.get("steps") or []
    ids = []
    for entry in steps:
        if isinstance(entry, dict):
            ids.append(str(entry.get("id", "")))
        else:
            ids.append(str(entry).split("#")[0].strip())
    # Also walk phases if present
    for phase in schema.get("phases") or []:
        for entry in phase.get("steps") or []:
            if isinstance(entry, dict):
                ids.append(str(entry.get("id", "")))
            else:
                ids.append(str(entry).split(" if ")[0].strip().split("#")[0].strip())
    return [s for s in ids if s]


def _find_contract(step_id: str, steps_dir: Path) -> Path | None:
    dir_form = steps_dir / step_id / "contract.yaml"
    if dir_form.is_file():
        return dir_form
    flat = steps_dir / f"{step_id}.yaml"
    if flat.is_file():
        return flat
    return None


def _check_contracts(step_ids: list[str], steps_dir: Path) -> None:
    missing = []
    violations = []
    for step_id in step_ids:
        contract_path = _find_contract(step_id, steps_dir)
        if contract_path is None:
            missing.append(step_id)
            continue
        with open(contract_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not (data.get("agent") or data.get("run")):
            violations.append(step_id)
    if missing:
        print("ERROR: missing contracts:", file=sys.stderr)
        for s in missing:
            print(f"  - {s}", file=sys.stderr)
        raise SystemExit(1)
    if violations:
        print("ERROR: contracts missing agent: and run:", file=sys.stderr)
        for s in violations:
            print(f"  - {s}", file=sys.stderr)
        raise SystemExit(1)


def _smoke_expand(schema_name: str, step_ids: list[str], repo_root: str) -> None:
    """Write a synthetic state.yaml and run generate_plan on it."""
    from orchestrator_next.generate_plan import generate_plan

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state: dict[str, Any] = {
        "change_id": "wf-validate",
        "slug": "wf-validate",
        "schema": schema_name,
        "status": "active",
        "repo_root": repo_root,
        "flags": {},
        "workflow_plan": {"main": {"active": step_ids, "filtered": []}},
        "phase": "main",
        "step_history": [],
        "created_at": now,
    }
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "state.yaml")
        with open(state_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(state, f, sort_keys=False)
        os.environ["ORCHESTRATOR_HOME"] = repo_root
        try:
            generate_plan(state_path)
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: expand-plan failed: {exc}", file=sys.stderr)
            raise SystemExit(1)


def validate_workflow(schema_name: str, repo_root: str) -> None:
    wf_path = _config_root(repo_root) / "workflows" / f"{schema_name}.yaml"
    print(f"Checking workflow: {wf_path}", file=sys.stderr)

    schema = _load_schema(schema_name, repo_root)
    step_ids = _step_ids(schema)
    _check_contracts(step_ids, _steps_dir(repo_root))

    if schema_name in _SKIP_EXPAND:
        print(f"OK: contracts valid ({schema_name} — expand-plan smoke skipped)", file=sys.stderr)
        return

    _smoke_expand(schema_name, step_ids, repo_root)
    print(f"OK: {schema_name} — contracts valid, expand-plan succeeded", file=sys.stderr)


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]
    if not args:
        print("usage: orchestrator validate-workflow <schema-name>", file=sys.stderr)
        return 1
    schema_name = args[0]
    repo_root = os.environ.get("ORCHESTRATOR_HOME") or str(Path(__file__).resolve().parents[1])
    try:
        validate_workflow(schema_name, repo_root)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
