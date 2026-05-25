"""
Parser for state.yaml and step contracts.

Produces a State dataclass from a state.yaml path. Resolves step contracts
from ORCHESTRATOR_HOME/config/steps/<step_id>.yaml with a test override
via ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE env var.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_COMPLEXITY_VALUES = frozenset({"XS", "S", "M", "L", "XL"})


class ContractError(ValueError):
    """Raised when a step contract is structurally invalid (HL-287 M2)."""


class ContractDispatchError(ValueError):
    """Raised when a step contract has neither agent: nor run: (ORC-45)."""


@dataclass
class StepContract:
    """Contract fields needed by the dispatcher.

    `inputs` and `outputs` are typed I/O specs for this step (ORC-76 Stage B).
    Each item is a dict with keys: name (str), path (str|None), optional (bool).
    Typed entries (path is not None) use `<slug>` as a placeholder for change_id.

    Backward-compatible: contracts that don't declare the fields get `[]`.
    `legacy_input_names` / `legacy_output_names` hold the flat name strings used
    by dispatch._resolve_inputs and record._check_declared_outputs until those
    callers migrate to the typed form (T-16, T-18).
    """
    id: str
    agent: str | None  # None = not declared (ORC-45: use run: path)
    run: str | None  # None = inline-only
    instruction: str
    rules: list[str]
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    # ORC-63 AC-5: names declared optional via a `{name: optional}` inputs
    # item. An optional input never blocks dispatch. A subset of legacy_input_names.
    optional_inputs: list[str] = field(default_factory=list)
    # ORC-76 back-compat: flat name strings for dispatch/_check_declared_outputs.
    # Populated alongside inputs/outputs; removed once T-16/T-18 fully migrate.
    legacy_input_names: list[str] = field(default_factory=list)
    legacy_output_names: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    inline: bool = False  # HL-287 M3: inline: true + run: <script> path
    repeat_until: str | None = None  # ISSUE-16: predicate name gating advance
    # ORC-76: explicit kind; synthesized from run: presence for flat-file form
    kind: str = ""  # "agent" | "script" | "" (legacy/unset)

    def typed_input_paths(self, state: "State") -> list[tuple[str, str]]:
        """Resolve typed inputs to (name, abs_path) pairs for the given state.

        Substitutes `<slug>` with `state.change_id` and joins against
        `state.worktree_artifact_dir` (or `state.repo_root` as fallback).
        Only entries where path is not None are returned — legacy inputs
        (path=None) are omitted.

        ORC-76 T-14: AC-3, AC-9.
        """
        base = state.worktree_artifact_dir or state.repo_root
        result: list[tuple[str, str]] = []
        for spec in self.inputs:
            path = spec.get("path")
            if path is None:
                continue
            resolved = path.replace("<slug>", state.change_id)
            abs_path = os.path.join(base, resolved) if base else resolved
            result.append((spec["name"], abs_path))
        return result


@dataclass
class StepHistoryEntry:
    """One entry from step_history[] in state.yaml."""
    step_id: str
    phase: str
    status: str
    agent: str
    attempt: int | None
    started_at: str | None
    ended_at: str | None  # accepts completed_at as fallback
    usage: dict[str, Any]
    escalation: dict[str, Any] | None
    raw: dict[str, Any]  # full entry for upsert


@dataclass
class State:
    """Parsed view of a state.yaml file."""
    change_id: str
    phase: str
    repo_root: str  # resolved ORCHESTRATOR_REPO_ROOT
    workflow_dir: str  # worktree_path or resolved dir
    workflow_plan: dict[str, Any]  # raw workflow_plan
    step_history: list[StepHistoryEntry]
    raw: dict[str, Any]  # full state.yaml for any extra fields
    complexity: str | None = None  # HL-291: optional closed set {XS,S,M,L,XL}
    worktree_artifact_dir: str = ""  # HL-303: base path for tracked artifacts (spec/design/tasks/diagnose)


def _contract_search_dirs(state_yaml_path: str) -> list[str]:
    """Return ordered list of directories to search for step contracts."""
    dirs: list[str] = []

    # Test override: explicit dir for fixture step contracts
    override = os.environ.get("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE")
    if override:
        dirs.append(override)
        return dirs  # In test mode, only search the override dir

    # Repo override (workflow-local steps): $REPO_WORKFLOW_DIR/config/steps/
    workflow_dir = os.environ.get("ORCHESTRATOR_WORKFLOW_DIR", "")
    if workflow_dir:
        dirs.append(os.path.join(workflow_dir, "config", "steps"))

    # Canonical: $ORCHESTRATOR_HOME/config/steps/
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if home:
        dirs.append(os.path.join(home, "config", "steps"))

    return dirs


def _parse_contract_fields(
    step_id: str,
    data: dict[str, Any],
    kind: str,
    run: str | None,
    instruction: str,
) -> StepContract:
    """Build a StepContract from raw YAML data and pre-resolved values.

    Handles inputs/outputs coercion and optional_inputs extraction.
    Called by both the directory-form and flat-file branches.
    """
    # M2: contracts MUST declare `inputs:` and `outputs:` (may be
    # empty list, may not be absent). Missing raises ContractError.
    # Exception: inline-script steps (run: or inline: true) default
    # to [] — they pass data via env vars, not structured I/O.
    is_inline_script = data.get("inline") is True or bool(run)
    if "inputs" not in data:
        if is_inline_script:
            data["inputs"] = []
        else:
            raise ContractError(
                f"contract {step_id} is missing required `inputs:` field "
                f"(use `inputs: []` if the step needs none)"
            )
    if "outputs" not in data:
        if is_inline_script:
            data["outputs"] = []
        else:
            raise ContractError(
                f"contract {step_id} is missing required `outputs:` field "
                f"(use `outputs: []` if the step produces none)"
            )
    # ORC-76 Stage B: parse inputs into list[dict[str,Any]] with unified shape.
    # Each item becomes: {name: str, path: str|None, optional: bool}.
    #
    # Item forms recognized:
    #   1. Typed spec: `{name: ..., path: ...}` — may include `optional: true`.
    #   2. Legacy optional sugar (ORC-63 AC-5): `{<name>: "optional"}` (single-key
    #      mapping where the value is the string "optional").
    #   3. Legacy bare string: `"<name>"` — becomes {name, path: None}.
    #
    # Parallel list `legacy_input_names` carries the flat name strings for
    # dispatch._resolve_inputs / _check_required_inputs until T-16 migrates them.
    raw_inputs = data.get("inputs") or []
    raw_outputs = data.get("outputs") or []
    inputs: list[dict[str, Any]] = []
    optional_inputs: list[str] = []
    legacy_input_names: list[str] = []

    for item in raw_inputs:
        if isinstance(item, str):
            # Form 3: bare string
            inputs.append({"name": item, "path": None, "optional": False})
            legacy_input_names.append(item)
        elif isinstance(item, dict) and "path" in item:
            # Form 1: typed spec {name, path, optional?}
            name = str(item["name"])
            path = item["path"]
            optional = bool(item.get("optional", False))
            inputs.append({"name": name, "path": path, "optional": optional})
            legacy_input_names.append(name)
            if optional:
                optional_inputs.append(name)
        elif (
            isinstance(item, dict)
            and len(item) == 1
            and str(next(iter(item.values()))).strip().lower() == "optional"
        ):
            # Form 2: legacy {<name>: "optional"} sugar
            name = str(next(iter(item.keys())))
            inputs.append({"name": name, "path": None, "optional": True})
            legacy_input_names.append(name)
            optional_inputs.append(name)
        else:
            # Unknown form: coerce name to str for maximum back-compat
            name = str(item)
            inputs.append({"name": name, "path": None, "optional": False})
            legacy_input_names.append(name)

    # Parse outputs into the same unified dict shape.
    # Typed spec: {name, path, optional?}. Legacy bare string: {name, path: None}.
    outputs: list[dict[str, Any]] = []
    legacy_output_names: list[str] = []
    for item in raw_outputs:
        if isinstance(item, str):
            outputs.append({"name": item, "path": None, "optional": False})
            legacy_output_names.append(item)
        elif isinstance(item, dict) and "path" in item:
            name = str(item["name"])
            path = item["path"]
            optional = bool(item.get("optional", False))
            outputs.append({"name": name, "path": path, "optional": optional})
            legacy_output_names.append(name)
        else:
            name = str(item)
            outputs.append({"name": name, "path": None, "optional": False})
            legacy_output_names.append(name)

    # allowed_tools: absent or null -> []; explicit list -> list
    raw_allowed = data.get("allowed_tools", []) or []
    allowed_tools = [str(x) if not isinstance(x, str) else x for x in raw_allowed]
    return StepContract(
        id=data.get("id", step_id),
        agent=data.get("agent") or None,
        run=run,
        instruction=instruction,
        rules=data.get("rules", []),
        inputs=inputs,
        outputs=outputs,
        optional_inputs=optional_inputs,
        legacy_input_names=legacy_input_names,
        legacy_output_names=legacy_output_names,
        allowed_tools=allowed_tools,
        inline=bool(data.get("inline", False)),
        repeat_until=data.get("repeat_until"),
        kind=kind,
    )


def _contract_lookup_id(step_id: str, state_yaml_path: str) -> str:
    """Return the contract file id to load for a workflow plan step.

    Task nodes use ids like ``task-T-1`` but declare ``step_contract:
    execute-one-task`` on the node. Without this indirection, dispatch would
    miss the contract and fall back to agent ``inline``.
    """
    try:
        with open(state_yaml_path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except OSError:
        return step_id

    plan = raw.get("workflow_plan") or {}
    if not isinstance(plan, dict):
        return step_id

    for phase_plan in plan.values():
        if not isinstance(phase_plan, dict):
            continue
        nodes = phase_plan.get("nodes")
        if not nodes:
            continue
        for node in nodes:
            if not isinstance(node, dict) or str(node.get("id", "")) != step_id:
                continue
            step_contract = node.get("step_contract")
            if isinstance(step_contract, str) and step_contract.strip():
                return step_contract.strip()
            return step_id
    return step_id


def _load_contract(step_id: str, state_yaml_path: str) -> StepContract:
    """Load and parse a step contract YAML, searching in priority order.

    For each search directory, the directory form (<id>/contract.yaml) is
    checked BEFORE the flat-file form (<id>.yaml). This preserves override
    precedence while preferring the new layout when both exist.
    """
    lookup_id = _contract_lookup_id(step_id, state_yaml_path)
    search_dirs = _contract_search_dirs(state_yaml_path)
    for d in search_dirs:
        # ── Directory form: <d>/<lookup_id>/contract.yaml ──────────────────────
        dir_contract = os.path.join(d, lookup_id, "contract.yaml")
        if os.path.isfile(dir_contract):
            contract_dir = os.path.join(d, lookup_id)
            with open(dir_contract, "r") as f:
                data = yaml.safe_load(f)

            # kind is required; must be 'agent' or 'script'
            kind = data.get("kind")
            if not kind or kind not in {"agent", "script"}:
                raise ContractError(
                    f"contract {step_id} missing kind: field (agent|script)"
                )

            if kind == "agent":
                prompt_path = os.path.join(contract_dir, "prompt.md")
                if not os.path.isfile(prompt_path):
                    raise ContractError(
                        f"agent contract {step_id} missing prompt.md"
                    )
                with open(prompt_path, "r") as f:
                    instruction = f.read()
                run = None
            else:  # kind == "script"
                run_rel = data.get("run")
                if not run_rel:
                    raise ContractError(
                        f"script contract {step_id} missing run: field"
                    )
                # Resolve relative paths against the contract directory
                if os.path.isabs(run_rel):
                    run = run_rel
                else:
                    run = os.path.join(contract_dir, run_rel)
                if not os.path.isfile(run):
                    raise ContractDispatchError(
                        f"script contract {step_id} missing script payload: {run}"
                    )
                instruction = ""

            return _parse_contract_fields(step_id, data, kind, run, instruction)

        # ── Flat-file form (legacy): <d>/<lookup_id>.yaml ──────────────────────
        flat_candidate = os.path.join(d, f"{lookup_id}.yaml")
        if os.path.isfile(flat_candidate):
            with open(flat_candidate, "r") as f:
                data = yaml.safe_load(f)
            # Synthesize kind from presence of run:
            kind = "script" if data.get("run") else "agent"
            run = data.get("run")
            instruction = data.get("instruction", "")
            return _parse_contract_fields(step_id, data, kind, run, instruction)

    raise FileNotFoundError(
        f"Step contract not found for '{step_id}'"
        + (f" (lookup '{lookup_id}')" if lookup_id != step_id else "")
        + f". Searched: {search_dirs}"
    )


def _parse_history_entry(raw: dict[str, Any]) -> StepHistoryEntry:
    """Parse a raw step_history entry dict into a typed dataclass."""
    # ended_at is the canonical name; completed_at is the alias during migration
    ended_at = raw.get("ended_at") or raw.get("completed_at")
    return StepHistoryEntry(
        step_id=raw.get("step_id", ""),
        phase=raw.get("phase", ""),
        status=raw.get("status", ""),
        agent=raw.get("agent", "inline"),
        attempt=raw.get("attempt"),
        started_at=raw.get("started_at"),
        ended_at=str(ended_at) if ended_at is not None else None,
        usage=raw.get("usage", {}),
        escalation=raw.get("escalation"),
        raw=raw,
    )


def load_state(state_yaml_path: str) -> State:
    """
    Parse state.yaml at the given path and return a State object.

    Does NOT load step contracts — those are loaded on demand by dispatch.py.
    """
    path = Path(state_yaml_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"state.yaml not found: {state_yaml_path}")

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"state.yaml is not a YAML mapping: {state_yaml_path}")

    change_id = raw.get("change_id", "")
    phase = raw.get("phase", "")
    workflow_dir = str(raw.get("worktree_path", ""))

    # HL-291: validate complexity against closed set; coerce unknown to None
    complexity = raw.get("complexity")
    if complexity is not None and complexity not in _COMPLEXITY_VALUES:
        sys.stderr.write(
            f"[complexity] ignoring unknown value {complexity!r} "
            f"for {raw.get('change_id', '<unknown>')}\n"
        )
        complexity = None

    # Expand ~ in worktree_path
    if workflow_dir.startswith("~"):
        workflow_dir = os.path.expanduser(workflow_dir)

    # repo_root resolution order: env var > state.yaml repo_root field > "".
    # HL-287 fix: state.yaml's repo_root is authoritative when env isn't set
    # (the state file records the repo at workflow init; env vars may be
    # absent when the CLI is invoked from a different cwd).
    repo_root = (
        os.environ.get("ORCHESTRATOR_REPO_ROOT")
        or str(raw.get("repo_root") or "")
    )

    # HL-303: worktree_artifact_dir points to $WORKTREE_ROOT/spec/changes when
    # worktree_path is set; falls back to $REPO_ROOT/spec/changes otherwise.
    worktree_path = raw.get("worktree_path", "")
    repo_root_raw = str(raw.get("repo_root") or "")
    artifact_base = worktree_path or repo_root_raw
    if artifact_base.startswith("~"):
        artifact_base = os.path.expanduser(artifact_base)
    worktree_artifact_dir = os.path.join(artifact_base, "spec", "changes") if artifact_base else ""

    history_raw = raw.get("step_history") or []
    step_history = [_parse_history_entry(e) for e in history_raw if isinstance(e, dict)]

    return State(
        change_id=change_id,
        phase=phase,
        repo_root=repo_root,
        workflow_dir=workflow_dir,
        workflow_plan=raw.get("workflow_plan", {}),
        step_history=step_history,
        raw=raw,
        complexity=complexity,
        worktree_artifact_dir=worktree_artifact_dir,
    )


def load_contract_for_step(step_id: str, state_yaml_path: str) -> StepContract:
    """Public convenience wrapper around _load_contract."""
    return _load_contract(step_id, state_yaml_path)


def phase_nodes(state: State, phase: str) -> list[dict]:
    """Return the plan node list for a phase (ORC-63 single read path).

    `workflow_plan[phase]` is read in this order:
      1. A `nodes:` list — returned verbatim (the post-promotion shape).
      2. A legacy `active:[ids]` list — each id is synthesized into a bare
         `{id, status: 'pending'}` node so an in-flight workflow that predates
         node promotion still dispatches (AC-11, design.md OQ-6).
      3. Neither present — returns `[]`.

    Pure read — no state mutation.
    """
    phase_plan = state.workflow_plan.get(phase, {})
    if not isinstance(phase_plan, dict):
        return []
    nodes = phase_plan.get("nodes")
    if nodes is not None:
        return list(nodes)
    active = phase_plan.get("active")
    if active is not None:
        return [{"id": sid, "status": "pending"} for sid in active]
    return []
