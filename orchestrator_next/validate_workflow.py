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
    """Step IDs from a schema's top-level steps list, gates excluded.

    A gate has no ``steps/<id>/`` directory — the recipe entry is the whole
    contract — so contract and wiring checks must not look for one.
    ``_check_gates`` validates gates instead.
    """
    from orchestrator_next.workflow_steps import is_gate_entry, step_id_of

    ids = []
    for entry in schema.get("steps") or []:
        if is_gate_entry(entry):
            continue
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


# A write that provisions the run's own workspace: worktree create/remove,
# state archive. It cannot sit behind a gate because it is what builds the
# place the gate's artifacts live — requiring one would make every recipe
# unstartable. Every other `write:*` (git, ticket, anything a pack invents)
# still needs an approval upstream.
WORKSPACE_WRITE = "write:workspace"


def _gated_writes(contract: Any) -> list[str]:
    """The step's ``write:*`` side effects that a gate must authorize."""
    return [
        e for e in (getattr(contract, "side_effects", None) or [])
        if str(e).startswith("write:") and str(e) != WORKSPACE_WRITE
    ]


def _check_gates(schema_name: str, schema: dict[str, Any]) -> None:
    """A step with a ``write:*`` side effect must sit behind a gate.

    ``write:workspace`` is exempt — see ``WORKSPACE_WRITE``.

    Two ways to satisfy it, both checked in recipe (topological) order:

    1. some ``{gate: ...}`` entry appears earlier in the recipe, or
    2. the step declares ``requires: <name>`` matching an earlier gate's
       ``approve_as``.

    Form 2 is the precise one — it names *which* approval authorizes the write —
    so a `requires:` that names no upstream gate is itself an error.

    `signoff_policy` (protocol v1's phase-boundary approval knob) has no live
    reader left in the engine; a recipe still carrying one gets a deprecation
    warning pointing at gates, not an implicit gate. Synthesizing a gate the
    author never wrote would park runs at a step nobody expects.
    """
    from orchestrator_next.workflow_steps import is_gate_entry, normalize_step_entry

    entries = schema.get("steps") or []
    if schema.get("signoff_policy"):
        print(
            "WARN: signoff_policy: is deprecated and ignored — declare an "
            "explicit {gate: <id>, show: [...], approve_as: <token>} entry "
            "instead (docs/protocol-v2.md §7)",
            file=sys.stderr,
        )

    seen_gate = False
    gate_tokens: set[str] = set()
    errors: list[str] = []

    for entry in entries:
        if is_gate_entry(entry):
            seen_gate = True
            approve_as = str(normalize_step_entry(entry).get("approve_as") or "")
            if not approve_as:
                errors.append(
                    f"gate {normalize_step_entry(entry).get('id')!r}: "
                    "approve_as: is required (it names the token downstream "
                    "steps declare in requires:)"
                )
            else:
                gate_tokens.add(approve_as)
            continue

        normalized = normalize_step_entry(entry)
        step_id = str(normalized.get("id") or "")
        if not step_id:
            continue

        requires = str(normalized.get("requires") or "")
        if requires and requires not in gate_tokens:
            errors.append(
                f"{step_id}: requires: {requires!r} names no upstream gate "
                f"(no earlier entry declares approve_as: {requires})"
            )

        try:
            contract = load_contract_for_step(step_id)
        except (FileNotFoundError, ContractError):
            continue  # _check_contracts already reported this
        writes = _gated_writes(contract)
        if writes and not (requires or seen_gate):
            errors.append(
                f"{step_id}: side_effects {writes} write outside the run but no "
                f"gate precedes the step — add a {{gate: ...}} entry before it, "
                f"or requires: <token> naming an upstream gate"
            )

    if errors:
        print("ERROR: gates before writes:", file=sys.stderr)
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
    _check_gates(schema_name, schema)

    if schema_name in _SKIP_EXPAND:
        print(f"OK: contracts valid ({schema_name} — expand-plan smoke skipped)", file=sys.stderr)
        return

    _smoke_expand(schema_name, step_ids, repo_root)
    print(f"OK: {schema_name} — contracts valid, expand-plan succeeded", file=sys.stderr)


def _split_diagnostics(text: str) -> tuple[list[str], list[str]]:
    """Split captured stderr into (errors, warnings).

    Every check already writes a human-readable diagnostic line; ``--json``
    reuses those rather than threading a second result type through each
    check. An ``ERROR:`` header is followed by ``  - <detail>`` bullets, which
    carry the specifics, so a header with bullets under it is dropped in
    favor of them.
    """
    errors: list[str] = []
    warnings: list[str] = []
    pending_header = ""
    for line in text.splitlines():
        if line.startswith("  - "):
            errors.append(line[4:].strip())
            pending_header = ""
            continue
        if pending_header:
            errors.append(pending_header)
            pending_header = ""
        if line.startswith("ERROR: "):
            pending_header = line[len("ERROR: "):].strip()
        elif line.startswith("WARN: "):
            warnings.append(line[len("WARN: "):].strip())
    if pending_header:
        errors.append(pending_header)
    return errors, warnings


def main(args: list[str] | None = None) -> int:
    import contextlib
    import io
    import json

    if args is None:
        args = sys.argv[1:]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if not args:
        print("usage: orchestrator validate-workflow <schema-name> [--json]",
              file=sys.stderr)
        return 1
    schema_name = args[0]
    repo_root = os.environ.get("ORCHESTRATOR_HOME") or str(Path(__file__).resolve().parents[1])
    from orchestrator_next.paths import ConfigRootError

    captured = io.StringIO()
    # --json speaks to a script, so the diagnostics belong in the document,
    # not interleaved on stderr. Without it nothing is captured and the
    # human-readable output is byte-identical to before.
    redirect = contextlib.redirect_stderr(captured) if as_json \
        else contextlib.nullcontext()

    code = 0
    with redirect:
        try:
            validate_workflow(schema_name, repo_root)
        except ConfigRootError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            code = 1
        except SystemExit as exc:
            code = int(exc.code) if exc.code is not None else 1

    if as_json:
        errors, warnings = _split_diagnostics(captured.getvalue())
        print(json.dumps({"ok": code == 0, "errors": errors,
                          "warnings": warnings}, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
