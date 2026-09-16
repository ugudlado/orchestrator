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

from orchestrator_next.parser import (
    AgentStepContract,
    ContractError,
    load_contract_for_step,
)

# Schemas that have no standard seed shape — skip the generate_plan smoke.
_SKIP_EXPAND = {"complete"}


def _load_schema(schema_name: str) -> dict[str, Any]:
    from orchestrator_next.paths import config_root
    path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        print(f"ERROR: workflow not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _step_ids(schema: dict[str, Any]) -> list[str]:
    """Extract step IDs from a schema's top-level steps list."""
    from orchestrator_next.workflow_steps import step_id_of

    ids = []
    for entry in schema.get("steps") or []:
        sid = step_id_of(entry)
        if sid:
            ids.append(sid)
    return ids


def _check_contracts(step_ids: list[str]) -> None:
    """Load each step contract via the parser; report missing or invalid contracts.

    Contract loading is where protocol-v2 shape errors surface: an unknown
    ``kind:``, a malformed ``in:``/``out:`` block, or a bad ``tools:`` list all
    raise ContractError in the parser and fail here. A judgment step with no
    ``out:`` is only a warning while the pack migrates (plan Phase 1.2).
    """
    missing = []
    invalid = []
    for step_id in step_ids:
        try:
            contract = load_contract_for_step(step_id)
        except FileNotFoundError:
            missing.append(step_id)
        except ContractError as exc:
            invalid.append((step_id, str(exc)))
        else:
            if isinstance(contract, AgentStepContract) and not contract.outputs:
                print(
                    f"WARN: {step_id}: judgment step declares no out: — "
                    "still using the legacy COMPLETION block (protocol v2 §10)",
                    file=sys.stderr,
                )
    if missing:
        print("ERROR: missing contracts:", file=sys.stderr)
        for s in missing:
            print(f"  - {s}", file=sys.stderr)
        print("Contract layout: config/steps/<id>/contract.yaml with prompt: or run:.", file=sys.stderr)
        raise SystemExit(1)
    if invalid:
        print("ERROR: invalid contracts:", file=sys.stderr)
        for s, reason in invalid:
            print(f"  - {s}: {reason}", file=sys.stderr)
        print("Contract layout: config/steps/<id>/contract.yaml with prompt: or run:.", file=sys.stderr)
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


def _charter_text(contract: Any) -> str:
    """The step's charter body, or "" for a step that has no prompt."""
    return str(getattr(contract, "instruction", "") or "")


def _check_wiring(schema_name: str, step_ids: list[str]) -> None:
    """Every ``in:`` artifact must be produced upstream, or be a recipe input.

    Walks the recipe in declaration order, accumulating the artifact names each
    step declares in ``out:``. A step whose ``in:`` names something no earlier
    step produces (and that the recipe does not declare under ``inputs:``) is a
    wiring error, unless that input is marked ``optional: true``.

    Also rejects a charter that templates an ``{in.x}`` / ``{out.y}`` the step's
    own contract never declared.
    """
    from orchestrator_next.parser import ContractError as _CE
    from orchestrator_next.parser import load_recipe
    from orchestrator_next.protocol import placeholder_names

    try:
        recipe = load_recipe(schema_name)
    except (FileNotFoundError, _CE) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    produced: set[str] = set(recipe.inputs)
    errors: list[str] = []
    unmigrated: list[str] = []

    for step_id in step_ids:
        try:
            contract = load_contract_for_step(step_id)
        except (FileNotFoundError, ContractError):
            continue  # _check_contracts already reported this
        inputs = getattr(contract, "inputs", None) or {}
        outputs = getattr(contract, "outputs", None) or {}
        if not inputs and not outputs:
            # An unmigrated step declares no I/O at all, so the engine cannot
            # see what it produces. Treat it as an unknown producer rather than
            # reporting every downstream in: as unwired (plan Phase 1.2 is
            # still mid-migration in the pack).
            unmigrated.append(step_id)
            continue

        for name, spec in sorted(inputs.items()):
            if not spec.get("artifact"):
                continue  # a scalar in: is supplied by the harness, not a file
            if spec.get("optional"):
                continue
            if name not in produced:
                if unmigrated:
                    # Some upstream step is unmigrated and may well write this
                    # file; the engine cannot prove it either way, so warn.
                    print(
                        f"WARN: {step_id}: in.{name} has no declared producer — "
                        f"upstream steps {', '.join(unmigrated)} declare no out: yet",
                        file=sys.stderr,
                    )
                    continue
                errors.append(
                    f"{step_id}: in.{name} is not produced by any upstream step "
                    f"(add it to the recipe's inputs:, mark it optional:, or "
                    f"declare it in an earlier step's out:)"
                )

        declared = set(inputs) | set(outputs)
        for side, name in sorted(placeholder_names(_charter_text(contract))):
            if name not in declared:
                errors.append(
                    f"{step_id}: charter references {{{side}.{name}}} but the "
                    f"contract declares no {side}: entry named {name!r}"
                )

        produced |= set(outputs)

    if errors:
        print("ERROR: artifact wiring:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(1)


def validate_workflow(schema_name: str, repo_root: str) -> None:
    from orchestrator_next.paths import config_root
    wf_path = config_root() / "workflows" / f"{schema_name}.yaml"
    print(f"Checking workflow: {wf_path}", file=sys.stderr)

    schema = _load_schema(schema_name)
    step_ids = _step_ids(schema)
    _check_contracts(step_ids)
    _check_wiring(schema_name, step_ids)

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
