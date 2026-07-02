"""
Validate a workflow schema and its step contracts (ORC-*).

Checks:
  1. Schema file exists under config/workflows/<name>.yaml
  2. Every step has a loadable contract (contract.yaml with a valid model: or run: field)
  3. generate_plan succeeds on a synthetic state (skipped for operator schemas)

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

from orchestrator_next.parser import ContractError, load_contract_for_step

# Schemas that have no standard seed shape — skip the generate_plan smoke.
_SKIP_EXPAND = {"complete"}


def _load_schema(schema_name: str, repo_root: str) -> dict[str, Any]:
    from orchestrator_next.paths import config_root
    path = config_root() / "workflows" / f"{schema_name}.yaml"
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


def _check_contracts(step_ids: list[str], repo_root: str) -> None:
    """Load each step contract via the parser; report missing or invalid contracts."""
    # Use a dummy state path — _contract_lookup_id gets workflow_plan={} so it
    # never reads the file, but the path must be under repo_root so config_root()
    # resolves the right steps/ directory.
    dummy_state = os.path.join(repo_root, "state.yaml")
    missing = []
    invalid = []
    for step_id in step_ids:
        try:
            load_contract_for_step(step_id, dummy_state, workflow_plan={})
        except FileNotFoundError:
            missing.append(step_id)
        except ContractError as exc:
            invalid.append((step_id, str(exc)))
    if missing:
        print("ERROR: missing contracts:", file=sys.stderr)
        for s in missing:
            print(f"  - {s}", file=sys.stderr)
        raise SystemExit(1)
    if invalid:
        print("ERROR: invalid contracts:", file=sys.stderr)
        for s, reason in invalid:
            print(f"  - {s}: {reason}", file=sys.stderr)
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
        try:
            generate_plan(state_path)
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: expand-plan failed: {exc}", file=sys.stderr)
            raise SystemExit(1)


def validate_workflow(schema_name: str, repo_root: str) -> None:
    from orchestrator_next.paths import config_root
    wf_path = config_root() / "workflows" / f"{schema_name}.yaml"
    print(f"Checking workflow: {wf_path}", file=sys.stderr)

    schema = _load_schema(schema_name, repo_root)
    step_ids = _step_ids(schema)
    _check_contracts(step_ids, repo_root)

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
    from orchestrator_next.paths import ConfigRootError
    try:
        validate_workflow(schema_name, repo_root)
    except ConfigRootError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
