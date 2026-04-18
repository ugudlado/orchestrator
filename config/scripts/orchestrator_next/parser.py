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


class ContractError(ValueError):
    """Raised when a step contract is structurally invalid (HL-287 M2)."""


@dataclass
class StepContract:
    """Contract fields needed by the dispatcher.

    `inputs` and `outputs` declare the typed I/O for this step (HL-287 M1).
    Backward-compatible: contracts that don't declare the fields get `[]`.
    """
    id: str
    agent: str
    run: str | None  # None = inline-only
    instruction: str
    rules: list[str]
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    inline: bool = False  # HL-287 M3: inline: true + run: <script> path


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


def _load_contract(step_id: str, state_yaml_path: str) -> StepContract:
    """Load and parse a step contract YAML, searching in priority order."""
    search_dirs = _contract_search_dirs(state_yaml_path)
    for d in search_dirs:
        candidate = os.path.join(d, f"{step_id}.yaml")
        if os.path.isfile(candidate):
            with open(candidate, "r") as f:
                data = yaml.safe_load(f)
            # M2: contracts MUST declare `inputs:` and `outputs:` (may be
            # empty list, may not be absent). Missing raises ContractError.
            if "inputs" not in data:
                raise ContractError(
                    f"contract {step_id} is missing required `inputs:` field "
                    f"(use `inputs: []` if the step needs none)"
                )
            if "outputs" not in data:
                raise ContractError(
                    f"contract {step_id} is missing required `outputs:` field "
                    f"(use `outputs: []` if the step produces none)"
                )
            # Coerce to list[str]. M1 note: older contracts may have prose
            # bullets (with colons, parens) which yaml parses as dicts —
            # coerce to string for backward compatibility. Normalization to
            # bare identifier names is deferred to M2.5 follow-up polish.
            raw_inputs = data.get("inputs") or []
            raw_outputs = data.get("outputs") or []
            inputs = [str(x) if not isinstance(x, str) else x for x in raw_inputs]
            outputs = [str(x) if not isinstance(x, str) else x for x in raw_outputs]
            return StepContract(
                id=data.get("id", step_id),
                agent=data.get("agent", "inline"),
                run=data.get("run"),
                instruction=data.get("instruction", ""),
                rules=data.get("rules", []),
                inputs=inputs,
                outputs=outputs,
                inline=bool(data.get("inline", False)),
            )
    raise FileNotFoundError(
        f"Step contract not found for '{step_id}'. Searched: {search_dirs}"
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

    # Expand ~ in worktree_path
    if workflow_dir.startswith("~"):
        workflow_dir = os.path.expanduser(workflow_dir)

    # ORCHESTRATOR_REPO_ROOT from env, defaulting to empty (tests use "")
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT", "")

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
    )


def load_contract_for_step(step_id: str, state_yaml_path: str) -> StepContract:
    """Public convenience wrapper around _load_contract."""
    return _load_contract(step_id, state_yaml_path)
