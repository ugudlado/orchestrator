"""
Pure dispatcher: State → (action_dict, exit_code).

Protocol (ORC-45 two-path dispatch):
  exit 0 + JSON with agent key  → driver spawns Agent tool
  exit 0 + no JSON              → inline script ran and recorded; driver loops
  exit 1                        → workflow complete; driver reads state.yaml
  exit 2                        → step blocked; driver reads state.yaml
  exit 3                        → ContractDispatchError (missing agent: and run:)

No action field. No signal field. No verify_phase.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import readiness
from orchestrator_next.parser import (
    ContractError,
    ContractDispatchError as ParserContractDispatchError,
    State,
    StepContract,
    StepHistoryEntry,
    load_contract_for_step,
    phase_nodes,
)
def _step_in_plan(state, phase: str, step_id: str) -> bool:
    """True when step_id is a node in workflow_plan for this phase."""
    return any(str(n.get("id", "")) == step_id for n in phase_nodes(state, phase))


class ContractDispatchError(RuntimeError):
    """Missing step contract or agent file on disk; run /doctor to diagnose."""


def _agent_definition_path(agent_name: str) -> str | None:
    """Return the first existing agent .md path, or None if not found."""
    search_roots: list[str] = []
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if home:
        search_roots.append(home)
    search_roots.append(os.path.expanduser("~/.claude"))
    for root in search_roots:
        path = os.path.join(root, "agents", f"{agent_name}.md")
        if os.path.isfile(path):
            return path
    return None


def _load_step_contract(step_id: str, state_yaml_path: str) -> StepContract:
    """Load a step contract; map FileNotFoundError to ContractDispatchError."""
    try:
        return load_contract_for_step(step_id, state_yaml_path)
    except FileNotFoundError as e:
        raise ContractDispatchError(
            f"Step contract not found: {step_id}. Run /doctor to diagnose."
        ) from e


# Blocking statuses: caller cannot proceed
_BLOCKING_STATUSES = frozenset({"escalate_to_architect", "blocked"})
_DEFAULT_MAX_SPAWN_FAILURES = 3


def _compute_attempt(step_history: list[StepHistoryEntry], phase: str, step_id: str) -> int:
    """
    Compute the next attempt number for a (phase, step_id) pair.

    Scans step_history for all entries matching phase+step_id, finds the
    maximum attempt number, and returns max + 1 (default 1 when none exist).
    """
    attempts = [
        e.attempt
        for e in step_history
        if e.phase == phase and e.step_id == step_id and e.attempt is not None
    ]
    return (max(attempts) + 1) if attempts else 1


def _is_spawn_failure(entry: StepHistoryEntry) -> bool:
    """True when the entry is a pre-agent spawn failure (model=none, zero tokens)."""
    if entry.status != "failed":
        return False
    usage = entry.usage if isinstance(entry.usage, dict) else {}
    model = usage.get("model")
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return (
        model == "none"
        and input_tokens == 0
        and output_tokens == 0
    )


def _consecutive_spawn_failures(
    step_history: list[StepHistoryEntry], phase: str, step_id: str
) -> int:
    """Count trailing spawn failures for (phase, step_id) in step_history."""
    count = 0
    for entry in reversed(step_history):
        if entry.phase != phase or entry.step_id != step_id:
            continue
        if _is_spawn_failure(entry):
            count += 1
            continue
        break
    return count


def _project_yaml_path(state_raw: dict[str, Any]) -> Path | None:
    """Resolve the repo's project.yaml, worktree-first then repo_root (orc-85).

    Shared by callers that read project.yaml fields (quality_bar, learnings).
    Returns None when neither location yields an existing file.
    """
    worktree = state_raw.get("worktree_path")
    if isinstance(worktree, str) and worktree:
        wt = Path(os.path.expanduser(worktree))
        if wt.is_dir():
            candidate = wt / "spec" / "project.yaml"
            if candidate.is_file():
                return candidate
    repo_root = state_raw.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        candidate = Path(os.path.expanduser(repo_root)) / "spec" / "project.yaml"
        if candidate.is_file():
            return candidate
    return None


def _max_spawn_failures(state_raw: dict[str, Any]) -> int:
    """Read `quality_bar.max_spawn_failures` from the repo's project.yaml (orc-85)."""
    candidate = _project_yaml_path(state_raw)
    if candidate is not None:
        try:
            data = yaml.safe_load(candidate.read_text()) or {}
            quality_bar = data.get("quality_bar") if isinstance(data, dict) else None
            value = (
                quality_bar.get("max_spawn_failures")
                if isinstance(quality_bar, dict)
                else None
            )
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        except (yaml.YAMLError, OSError) as exc:
            sys.stderr.write(f"[dispatch] warning: could not read {candidate}: {exc}\n")

    sys.stderr.write(
        f"[dispatch] warning: quality_bar.max_spawn_failures not found in project.yaml; "
        f"defaulting to {_DEFAULT_MAX_SPAWN_FAILURES}\n"
    )
    return _DEFAULT_MAX_SPAWN_FAILURES


def _build_env(
    state: State,
    step_id: str,
    attempt: int,
    state_yaml_path: str = "",
) -> dict[str, str]:
    """Build the ORCHESTRATOR_* env block for the action response."""
    from orchestrator_next.step_env import build_dispatch_env

    return build_dispatch_env(state, step_id, attempt, state_yaml_path)


def _get_last_entry(step_history: list[StepHistoryEntry]) -> StepHistoryEntry | None:
    return step_history[-1] if step_history else None


def _typed_input_base_dir(state: State) -> str:
    """Repo or worktree root for path templates like spec/changes/<slug>/file.md.

    Typed I/O paths are relative to this root. seed-state sets worktree_path
    (every run is isolated in a worktree) but not worktree_artifact_dir in
    state.yaml; parser derives worktree_artifact_dir as <root>/spec/changes for
    legacy evidence only.
    """
    worktree_path = str(state.raw.get("worktree_path") or "").strip()
    if worktree_path:
        return os.path.expanduser(worktree_path) if worktree_path.startswith("~") else worktree_path
    explicit = str(state.raw.get("worktree_artifact_dir") or "").strip()
    if explicit:
        return os.path.expanduser(explicit) if explicit.startswith("~") else explicit
    return state.repo_root or ""


def _resolve_inputs(
    state: State, contract: StepContract
) -> tuple[dict[str, Any], list[str]]:
    """Resolve a contract's declared ``inputs:`` against prior step outputs.

    For typed inputs (path is not None): substitute ``<slug>`` with
    ``state.change_id``, join against ``state.worktree_artifact_dir``, and
    add ``{name: abs_path}`` to resolved iff the file exists.  Missing typed
    inputs (file absent) are added to ``missing`` only when not optional.

    For legacy inputs (path is None): walk ``state.step_history`` in reverse
    for a terminal ``completed`` entry whose ``evidence.outputs.<name>`` is
    set; fall back to ``state.raw`` top-level keys.

    Returns ``(resolved, missing)``. Missing names are not an error here —
    the caller decides. Contracts with empty ``inputs:`` return ``({}, [])``.
    """
    resolved: dict[str, Any] = {}
    missing: list[str] = []

    # ORC-76 T-16: typed paths join against repo/worktree root (see _typed_input_base_dir).
    raw_artifact_dir = _typed_input_base_dir(state)
    typed_names: set[str] = set()
    for spec in contract.inputs:
        path = spec.get("path")
        if path is None:
            continue
        name = spec["name"]
        optional = bool(spec.get("optional", False))
        typed_names.add(name)
        # Substitute <slug> and join against the raw artifact dir root
        resolved_rel = path.replace("<slug>", state.change_id)
        abs_path = os.path.join(raw_artifact_dir, resolved_rel) if raw_artifact_dir else resolved_rel
        if os.path.isfile(abs_path):
            resolved[name] = abs_path
        elif not optional:
            missing.append(name)
        # optional + absent → silently skip (no resolved entry, no missing)

    # Legacy inputs: walk evidence.outputs then state.raw
    legacy_names = [n for n in contract.legacy_input_names if n not in typed_names]
    for name in legacy_names:
        found = False
        for entry in reversed(state.step_history):
            if entry.status != "completed":
                continue
            outputs = (entry.raw.get("evidence") or {}).get("outputs") or {}
            if name in outputs:
                resolved[name] = outputs[name]
                found = True
                break
        if not found and name in state.raw:
            resolved[name] = state.raw[name]
            found = True
        if not found:
            missing.append(name)

    return resolved, missing


def _resolve_allowed_tools(contract: StepContract) -> list[str]:
    if contract.agent is None:
        if contract.allowed_tools:
            print(
                f"WARNING: allowed_tools on inline step {contract.id!r} ignored",
                file=sys.stderr,
            )
        return []

    if contract.allowed_tools:
        if _agent_definition_path(contract.agent) is None:
            raise ContractDispatchError(
                f"Agent definition not found: {contract.agent!r}. "
                f"Run /doctor to diagnose."
            )
        print(
            f"WARNING: cannot resolve agent {contract.agent!r} tools; "
            f"allowed_tools on step {contract.id!r} not enforced",
            file=sys.stderr,
        )
    return []


def _node_step_context(state: State, step_id: str) -> dict[str, Any]:
    """Return the plan node dict for (current phase, step_id) as step_context.

    ORC-63: the per-step data formerly held in plan.yaml now lives on the node
    in `state.workflow_plan[phase].nodes`. A legacy `active:[ids]` block yields
    a synthesized bare node (back-compat read path, AC-11).
    """
    for node in phase_nodes(state, state.phase):
        if str(node.get("id", "")) == step_id:
            return dict(node)
    return {"id": step_id}


def _load_learnings(state_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Read `learnings[]` from the repo's project.yaml. Empty list on any failure.

    project.yaml learnings are already repo-scoped — the file IS the repo. So
    no repo: filtering here (that lives in contract-rule metadata, not here).
    This just loads the raw list; relevance filtering is _relevant_learnings.
    """
    path = _project_yaml_path(state_raw)
    if path is None:
        return []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        sys.stderr.write(f"[dispatch] warning: could not read learnings from {path}: {exc}\n")
        return []
    learnings = data.get("learnings") if isinstance(data, dict) else None
    if not isinstance(learnings, list):
        return []
    items = [item for item in learnings if isinstance(item, dict)]
    # YAML parses `learned: 2026-04-09` into a date object, which is not JSON
    # serializable — the action payload must be. Round-trip through json with
    # default=str to flatten dates (and any other non-JSON scalar) to strings.
    return json.loads(json.dumps(items, default=str))


def _relevant_learnings(
    learnings: list[dict[str, Any]], agent_name: str, phase: str
) -> list[dict[str, Any]]:
    """Select which project learnings to inject into a given agent's context.

    Policy (tag-and-filter, untagged = universal):
      - Exclude `kind: informational` — reference data (e.g. external-benchmark-
        references), not behavioral guidance for an agent.
      - If a learning carries an optional `agents:` list, include it only when
        `agent_name` is in that list. If it carries `phases:`, include only when
        `phase` matches. A learning with both must match both.
      - Untagged learnings (no `agents:` and no `phases:`) are universal —
        injected for every agent. This degrades to inject-all today, since no
        learning is tagged yet, and tightens automatically as the learner adds
        tags going forward.

    Order is preserved. Returns [] to inject nothing.
    """
    selected: list[dict[str, Any]] = []
    for item in learnings:
        if item.get("kind") == "informational":
            continue
        agents = item.get("agents")
        if isinstance(agents, list) and agents and agent_name not in agents:
            continue
        phases = item.get("phases")
        if isinstance(phases, list) and phases and phase not in phases:
            continue
        selected.append(item)
    return selected


def _persist_node_status(state_yaml_path: str, phase: str, step_id: str, status: str) -> None:
    """Mark a node's status in state.yaml on disk via readiness.mark_node_status.

    A narrow state.yaml writer for the dispatch-time `in_progress` transition.
    No-op for a legacy `active:[ids]` block (no node dicts to mutate).
    """
    path = Path(state_yaml_path)
    try:
        with open(path, "rb") as f:
            pre_bytes = f.read()
        state_raw = yaml.safe_load(pre_bytes.decode("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    readiness.mark_node_status(state_raw, phase, step_id, status)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    # Post-write corruption guard: restore pre-write bytes if unparseable.
    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError:
        with open(path, "wb") as f:
            f.write(pre_bytes)


def _check_required_inputs(
    state: State, contract: StepContract, step_id: str
) -> int | None:
    """Return exit code 2 if a required input is unresolvable, else None.

    For typed inputs (path set): missing iff os.path.isfile(resolved_path)
    is False and optional is False.  Diagnostic names the resolved abs path.

    For legacy inputs: missing iff no prior completed step produced the name
    under evidence.outputs and the name is absent from state.raw.
    Inputs named in contract.optional_inputs never block.
    """
    _resolved, missing = _resolve_inputs(state, contract)
    optional = set(contract.optional_inputs)
    required_missing = [m for m in missing if m not in optional]
    if required_missing:
        # Build diagnostic: for typed inputs include the resolved abs path.
        raw_artifact_dir = _typed_input_base_dir(state)
        typed_paths: dict[str, str] = {}
        for spec in contract.inputs:
            path = spec.get("path")
            if path is None:
                continue
            name = spec["name"]
            resolved_rel = path.replace("<slug>", state.change_id)
            abs_path = os.path.join(raw_artifact_dir, resolved_rel) if raw_artifact_dir else resolved_rel
            typed_paths[name] = abs_path

        parts: list[str] = []
        for name in required_missing:
            if name in typed_paths:
                parts.append(f"{name!r} (expected file: {typed_paths[name]})")
            else:
                parts.append(repr(name))

        print(
            f"ERROR: step {step_id!r} blocked — required input(s) missing: "
            f"{', '.join(parts)}. "
            f"Typed inputs require the file to exist on disk; legacy inputs "
            f"require a prior completed step to have produced them under "
            f"evidence.outputs or a matching key in state.raw.",
            file=sys.stderr,
        )
        return 2
    return None


def dispatch(state: State, state_yaml_path: str) -> tuple[dict[str, Any], int]:
    """
    DAG-walk dispatcher: State → (action_dict, exit_code).

    ORC-63: selects the next step via `readiness.next_ready_node(state)` over
    `workflow_plan[phase].nodes` — no plan.yaml. Required-input prereqs are a
    hard block (exit 2). On a fresh selection the chosen node is marked
    `in_progress` in state.yaml.
    """
    last = _get_last_entry(state.step_history)

    # --- Check: last entry is a blocking status → exit 2, no JSON (ORC-45)
    if last is not None and last.phase == state.phase and last.status in _BLOCKING_STATUSES:
        return {}, 2

    # --- Check: last entry is in_progress → resume
    if (
        last is not None
        and last.phase == state.phase
        and last.status == "in_progress"
        and last.ended_at is None
    ):
        if not _step_in_plan(state, state.phase, last.step_id):
            print(
                f"ERROR: refusing to resume step {last.step_id!r} — "
                f"not in workflow_plan[{state.phase!r}].nodes "
                f"(likely ghost from prior schema or stale state.yaml entry).",
                file=sys.stderr,
            )
            return {}, 3
        step_id = last.step_id
        # Resume: keep the ORIGINAL attempt. DO NOT call _compute_attempt here —
        # it returns max+1 (retry semantics). Resume semantics require attempt unchanged.
        attempt = last.attempt if last.attempt is not None else 1
        try:
            contract = _load_step_contract(step_id, state_yaml_path)
        except ContractDispatchError:
            # Fall back to inline-only contract with minimal data
            contract = StepContract(
                id=step_id,
                agent=last.agent,
                run=None,
                instruction="",
                rules=[],
            )
        inputs_resolved, _missing = _resolve_inputs(state, contract)
        resolved_allowed_tools = _resolve_allowed_tools(contract)
        # Best-effort learnings injection (mirrors the fresh-dispatch path below);
        # a failure here must never block a resume.
        try:
            _resume_learnings = _relevant_learnings(
                _load_learnings(state.raw), contract.agent, state.phase
            )
        except Exception as exc:  # noqa: BLE001 — dispatch must not crash on this
            sys.stderr.write(f"[dispatch] warning: learnings injection skipped: {exc}\n")
            _resume_learnings = []
        action = {
            "step_id": step_id,
            "phase": state.phase,
            "attempt": attempt,
            "is_resume": True,
            "started_at": last.started_at,
            "agent": contract.agent,
            "learnings": _resume_learnings,
            "instruction": contract.instruction,
            "rules": contract.rules,
            "inputs": inputs_resolved,
            "expected_outputs": contract.legacy_output_names,
            "resolved_allowed_tools": resolved_allowed_tools,
            "env": _build_env(state, step_id, attempt, state_yaml_path),
            "step_context": _node_step_context(state, step_id),
        }
        return action, 0

    # --- DAG-walk: select the first ready node (declaration-order tiebreak)
    next_step_id = readiness.repeat_until_redispatch(state, state_yaml_path)
    if next_step_id is None:
        next_step_id = readiness.next_ready_node(state)

    # --- Check: no ready node → exit 1, no JSON (phase complete) (ORC-45)
    if next_step_id is None:
        # Warn if this phase is not the last in workflow_plan (driver must advance phase)
        plan = (state.workflow_plan or {})
        phase_names = list(plan.keys())
        if len(phase_names) > 1 and state.phase in phase_names:
            current_idx = phase_names.index(state.phase)
            remaining = phase_names[current_idx + 1:]
            if remaining:
                print(
                    f"WARNING: phase '{state.phase}' is complete but "
                    f"workflow_plan has other phases ({', '.join(remaining)}). "
                    f"Driver must advance state.yaml 'phase' field and re-run "
                    f"'orchestrator next' before completing workflow.",
                    file=sys.stderr,
                )
        return {}, 1

    # --- Load contract for the next step
    try:
        contract = _load_step_contract(next_step_id, state_yaml_path)
    except ContractDispatchError:
        # Fall back to inline-only contract with minimal data when the step
        # contract was deleted after workflow_plan was frozen at pre-dispatch init.
        # Mirrors the resume_step branch above.
        contract = StepContract(
            id=next_step_id,
            agent=None,
            run=None,
            instruction="",
            rules=[],
        )

    # --- Prerequisite hard block (AC-4): a required input that no prior
    #     completed step produced and that is absent from state.raw → exit 2.
    block_code = _check_required_inputs(state, contract, next_step_id)
    if block_code is not None:
        return {}, block_code

    spawn_failures = _consecutive_spawn_failures(
        state.step_history, state.phase, next_step_id
    )
    max_spawn_failures = _max_spawn_failures(state.raw)
    if spawn_failures >= max_spawn_failures:
        print(
            f"BLOCKED: spawn_failure_cap — {spawn_failures} consecutive zero-token "
            f"failures for {state.phase}/{next_step_id}",
            file=sys.stderr,
        )
        return {"reason": "spawn_failure_cap"}, 2

    attempt = _compute_attempt(state.step_history, state.phase, next_step_id)
    env = _build_env(state, next_step_id, attempt, state_yaml_path)
    inputs_resolved, _missing = _resolve_inputs(state, contract)
    resolved_allowed_tools = _resolve_allowed_tools(contract)
    step_context = _node_step_context(state, next_step_id)

    # ORC-45 two-path dispatch: agent: → spawn; run: → execute inline; else → error.
    if contract.agent:
        # Learnings injection is best-effort: a failure here must never block a
        # spawn. Degrade to no learnings rather than taking down the dispatcher.
        try:
            learnings = _relevant_learnings(
                _load_learnings(state.raw), contract.agent, state.phase
            )
        except Exception as exc:  # noqa: BLE001 — dispatch must not crash on this
            sys.stderr.write(f"[dispatch] warning: learnings injection skipped: {exc}\n")
            learnings = []
        action = {
            "step_id": next_step_id,
            "phase": state.phase,
            "attempt": attempt,
            "agent": contract.agent,
            "learnings": learnings,
            "instruction": contract.instruction,
            "rules": contract.rules,
            "inputs": inputs_resolved,
            "expected_outputs": contract.legacy_output_names,
            "resolved_allowed_tools": resolved_allowed_tools,
            "env": env,
            "step_context": step_context,
        }
    elif contract.run:
        # Inline script executed synchronously by CLI — no JSON emitted, exit 0
        action: dict[str, Any] = {
            "step_id": next_step_id,
            "phase": state.phase,
            "attempt": attempt,
            "run": contract.run,
            "instruction": contract.instruction,
            "rules": contract.rules,
            "inputs": inputs_resolved,
            "expected_outputs": contract.legacy_output_names,
            "resolved_allowed_tools": resolved_allowed_tools,
            "env": env,
            "step_context": step_context,
        }
        # ORC-76 AC-2: for directory-form contracts the parser pre-resolves
        # run to an absolute path. Expose the contract directory so
        # bin/orchestrator can resolve relative run: paths against it when
        # step_contract_dir is set (and for metadata inspection by callers).
        from orchestrator_next.step_runner import step_directory

        orch_home = os.environ.get("ORCHESTRATOR_HOME", "")
        if orch_home and contract.run:
            action["step_contract_dir"] = str(
                step_directory(next_step_id, contract, orch_home)
            )
        elif contract.run and os.path.isabs(contract.run):
            action["step_contract_dir"] = os.path.dirname(contract.run)
    else:
        raise ParserContractDispatchError(
            f"step_contract_missing_run: {next_step_id}"
        )

    # ORC-63: mark the chosen node in_progress in state.yaml (the one
    # status mutator). No-op for a legacy active:[ids] block.
    _persist_node_status(state_yaml_path, state.phase, next_step_id, "in_progress")
    return action, 0


def emit_json(obj: dict[str, Any]) -> str:
    """
    Emit the action dict as deterministic, sorted-keys, indented JSON.

    Always uses sort_keys=True and indent=2 for byte-identical output
    regardless of TTY — satisfies AC-1 (deterministic) and test byte-compare.
    """
    return json.dumps(obj, sort_keys=True, indent=2) + "\n"
