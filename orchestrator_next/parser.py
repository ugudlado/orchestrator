"""
Parser for state.yaml and step contracts.

Produces a State dataclass from a state.yaml path. Resolves step contracts
from ORCHESTRATOR_HOME/config/steps/<step_id>.yaml with a test override
via ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE env var.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import create_model



class ContractError(ValueError):
    """Raised when a step contract is structurally invalid."""


class ContractNotFoundError(ValueError):
    """Raised when a step contract script payload is missing or invalid."""


_SCHEMA_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}


def build_output_validator(schema: dict[str, Any]):
    """Build a Pydantic model from a contract output_schema dict.

    Schema format (YAML):
      output_schema:
        verdict: {type: str, required: true}
        score:   {type: int}

    Returns a pydantic model class, or None if schema is empty.
    """
    if not schema:
        return None
    fields: dict[str, Any] = {}
    for name, spec in schema.items():
        if not isinstance(spec, dict):
            continue
        type_name = spec.get("type", "str")
        py_type = _SCHEMA_TYPE_MAP.get(type_name, Any)
        required = bool(spec.get("required", False))
        if required:
            fields[name] = (py_type, ...)
        else:
            fields[name] = (py_type | None, None)
    if not fields:
        return None
    return create_model("OutputSchema", **fields)


@dataclass
class AgentStepContract:
    """Contract for steps dispatched to an agent subprocess."""
    id: str
    model: str | None
    instruction: str
    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)
    state_mutating: bool = False
    default_outputs: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)


@dataclass
class ScriptStepContract:
    """Contract for steps executed as inline scripts."""
    id: str
    run: str
    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)
    # When true, the driver records the step BEFORE running the script so
    # state.yaml is consistent even if the script moves or rewrites it.
    state_mutating: bool = False


StepContract = AgentStepContract | ScriptStepContract



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
    worktree_artifact_dir: str = ""  # base path for tracked artifacts (spec/design/tasks/diagnose)


def _contract_search_dirs() -> list[str]:
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

    # Canonical: the config root's steps/ dir (ORCHESTRATOR_CONFIG, else
    # ORCHESTRATOR_HOME/config, else <cwd>/config — see paths.config_root).
    from orchestrator_next.paths import config_root
    dirs.append(str(config_root() / "steps"))

    return dirs



def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _make_contract(
    step_id: str,
    data: dict[str, Any],
    run: str | None,
    instruction: str,
) -> StepContract:
    shared = dict(
        id=data.get("id", step_id),
        pre=_as_str_list(data.get("pre")),
        post=_as_str_list(data.get("post")),
        state_mutating=bool(data.get("state_mutating", False)),
    )
    if run is None:
        raw_defaults = data.get("default_outputs")
        default_outputs = raw_defaults if isinstance(raw_defaults, dict) else {}
        raw_schema = data.get("output_schema")
        output_schema = raw_schema if isinstance(raw_schema, dict) else {}
        return AgentStepContract(
            **shared,
            model=data.get("model") or None,
            instruction=instruction,
            default_outputs=default_outputs,
            output_schema=output_schema,
        )
    return ScriptStepContract(**shared, run=run)


def _contract_lookup_id(
    step_id: str,
    state_yaml_path: str,
    workflow_plan: dict | None = None,
) -> str:
    """Return the contract file id for step_id, following step_contract overrides.

    Task nodes use ids like ``task-T-1`` but declare ``step_contract:
    execute-one-task`` on the node. Without this indirection, dispatch would
    miss the contract and fall back to agent ``inline``.

    When ``workflow_plan`` is provided it is used directly, avoiding a
    redundant read of state.yaml.
    """
    plan = workflow_plan
    if plan is None:
        try:
            with open(state_yaml_path, "r") as f:
                raw = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
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


def load_contract_for_step(
    step_id: str,
    state_yaml_path: str,
    workflow_plan: dict | None = None,
) -> StepContract:
    """Load and parse a step contract YAML.

    Searches each configured directory for <id>/contract.yaml (directory form).
    """
    lookup_id = _contract_lookup_id(step_id, state_yaml_path, workflow_plan=workflow_plan)
    search_dirs = _contract_search_dirs()
    for d in search_dirs:
        dir_contract = os.path.join(d, lookup_id, "contract.yaml")
        if os.path.isfile(dir_contract):
            contract_dir = os.path.join(d, lookup_id)
            with open(dir_contract, "r") as f:
                data = yaml.safe_load(f)

            is_script = bool(data.get("run"))
            if is_script:
                run_rel = data.get("run")
                if os.path.isabs(run_rel):
                    run = run_rel
                else:
                    run = os.path.join(contract_dir, run_rel)
                if not os.path.isfile(run):
                    raise ContractNotFoundError(
                        f"script contract {step_id} missing script payload: {run}"
                    )
                instruction = ""
            else:
                prompt_path = os.path.join(contract_dir, "prompt.md")
                if not os.path.isfile(prompt_path):
                    raise ContractError(
                        f"step contract {step_id} missing prompt.md"
                    )
                with open(prompt_path, "r") as f:
                    instruction = f.read()
                run = None

            return _make_contract(step_id, data, run, instruction)

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
        agent=raw.get("agent"),
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
    workflow_dir = os.path.expanduser(str(raw.get("worktree_path", "") or ""))

    # repo_root: env var wins over state.yaml field (state file is authoritative
    # when env is absent; env may be set to override for multi-repo setups).
    repo_root = (
        os.environ.get("ORCHESTRATOR_REPO_ROOT")
        or str(raw.get("repo_root") or "")
    )

    # worktree_artifact_dir: $WORKTREE_ROOT/spec/changes, or $REPO_ROOT/spec/changes.
    repo_root_raw = str(raw.get("repo_root") or "")
    artifact_base = os.path.expanduser(workflow_dir or repo_root_raw)
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
        worktree_artifact_dir=worktree_artifact_dir,
    )


def safe_write_yaml(path: Path, state_raw: dict, pre_write_bytes: bytes) -> None:
    """Write state_raw to path as YAML, restoring pre_write_bytes on parse error.

    Raises yaml.YAMLError when the written file fails post-write verification.
    The caller is responsible for catching and handling the error.
    """
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    try:
        with open(path, encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError:
        with open(path, "wb") as f:
            f.write(pre_write_bytes)
        raise


def phase_nodes(state: State, phase: str) -> list[dict]:
    """Return the plan node list for a phase, or [] if not present.

    Pure read — no state mutation.
    """
    phase_plan = state.workflow_plan.get(phase, {})
    if not isinstance(phase_plan, dict):
        return []
    nodes = phase_plan.get("nodes")
    if nodes is not None:
        return list(nodes)
    return []


def compute_attempt(
    history: "list[StepHistoryEntry] | list[dict]",
    phase: str,
    step_id: str,
    *,
    include_in_progress: bool,
) -> int:
    """Return the next attempt number for (phase, step_id).

    Dispatch passes include_in_progress=True (counts placeholders so the
    outgoing action gets a unique number). Record passes False (placeholders
    are not completed attempts and must not inflate the recorded attempt).
    """
    attempts: list[int] = []
    for e in history:
        d = e.raw if isinstance(e, StepHistoryEntry) else e
        if not isinstance(d, dict):
            continue
        if d.get("phase") != phase or d.get("step_id") != step_id:
            continue
        attempt_val = d.get("attempt")
        if not attempt_val:
            continue
        if not include_in_progress and d.get("status") == "in_progress":
            continue
        attempts.append(attempt_val)
    return (max(attempts) + 1) if attempts else 1
