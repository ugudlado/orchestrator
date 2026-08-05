"""
Parser for state.yaml and step contracts.

Produces a State dataclass from a state.yaml path. Resolves step contracts
from $ORCHESTRATOR_CONFIG/steps/<step_id>.yaml with a test override
via ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE env var.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ContractError(ValueError):
    """Raised when a step contract is structurally invalid."""


class ContractNotFoundError(ValueError):
    """Raised when a step contract script payload is missing or invalid."""


@dataclass
class AgentStepContract:
    """Contract for steps dispatched to an agent subprocess."""
    id: str
    model: str | None
    instruction: str
    # Resolved prompt directory (skills/<name> or legacy step dir). Exported as
    # ORCHESTRATOR_PROMPT_DIR so learn can colocate scenarios beside the charter.
    prompt_dir: str | None = None
    state_mutating: bool = False
    default_outputs: dict = field(default_factory=dict)
    required_outputs_for_completed: list = field(default_factory=list)
    # When true, the driver pauses before this step and asks the client for
    # direction; the next prompt's text is injected as "User direction".
    await_input: bool = False


@dataclass
class ScriptStepContract:
    """Contract for steps executed as inline scripts."""
    id: str
    run: str
    # When true, the driver records the step BEFORE running the script so
    # state.yaml is consistent even if the script moves or rewrites it.
    state_mutating: bool = False
    # Script steps can also gate on user input (e.g. choose a target based on
    # the user's latest message).
    await_input: bool = False


StepContract = AgentStepContract | ScriptStepContract


_FRONTMATTER_DELIM = "---"


def strip_frontmatter(text: str) -> str:
    """Return the body of a SKILL.md (or any markdown) after YAML frontmatter.

    If the file does not start with a frontmatter block, return text unchanged.
    """
    if not text.startswith(_FRONTMATTER_DELIM):
        return text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            return "".join(lines[i + 1 :]).lstrip("\n")
    return text


def prompt_search_dirs() -> list[Path]:
    """Dirs searched to resolve ``prompt:`` refs (e.g. <name>/SKILL.md).

    Fixed order (repo→pack), no env knob besides the test override:

    1. ``<repo>/skills`` when ``ORCHESTRATOR_REPO_ROOT`` / ``REPO_ROOT`` is set
    2. ``<pack>/skills`` — sibling of the config root (``config_root().parent / "skills"``)

    Skills live beside ``config/``, never inside it. ``ORCHESTRATOR_SKILLS_TEST_OVERRIDE``
    is a test-only override (os.pathsep-separated).
    """
    explicit = os.environ.get("ORCHESTRATOR_SKILLS_TEST_OVERRIDE")
    if explicit:
        return [Path(p) for p in explicit.split(os.pathsep) if p]

    from orchestrator_next.paths import config_root

    dirs: list[Path] = []
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    if repo_root:
        dirs.append(Path(repo_root) / "skills")
    pack_skills = config_root().parent / "skills"
    if pack_skills not in dirs:
        dirs.append(pack_skills)
    return dirs


def resolve_prompt_file(prompt_ref: str) -> Path:
    """Return the prompt ``.md`` file resolved through ``prompt_search_dirs()``.

    ``prompt:`` is a relative path to a markdown file: ``<name>/SKILL.md``
    (skill conventions — frontmatter stripped, colocated scenarios/learnings)
    or any other ``.md`` file (loaded verbatim). Directory names are rejected;
    the contract names the file itself.
    """
    ref = prompt_ref.strip()
    rel = Path(ref)
    if not ref or rel.is_absolute() or ".." in rel.parts:
        raise ContractError(
            f"prompt: must be a relative .md path (got {prompt_ref!r})"
        )
    if rel.suffix != ".md":
        raise ContractError(
            f"prompt: must point at a .md file, e.g. {ref}/SKILL.md "
            f"or {ref}/prompt.md (got {prompt_ref!r})"
        )
    searched = prompt_search_dirs()
    for root in searched:
        candidate = root / rel
        if candidate.is_file():
            return candidate.resolve()
    raise ContractError(
        f"prompt {ref!r} not found (searched: "
        + ", ".join(str(d) for d in searched)
        + ")"
    )


def _extends_ref(text: str) -> str | None:
    """The frontmatter ``extends:`` value, or None. Cheap line scan — no YAML lib."""
    if not text.startswith(_FRONTMATTER_DELIM):
        return None
    for line in text.splitlines()[1:]:
        if line.strip() == _FRONTMATTER_DELIM:
            return None
        if line.startswith("extends:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _base_role_line(skill_dir: Path, ref: str) -> str | None:
    """Instruction pointing the agent at the base role prompt, or None.

    The engine never downloads or composes the ``extends`` hierarchy — it just
    resolves a path ref and tells the agent to read it. Two roots tried in
    order: the skill's own dir (local override), then the downloaded pack
    root ``~/.orchestrator/pack`` (global base roles, e.g. ``developer``).
    git+ refs and missing paths are skipped (behavior identical to before).
    """
    if ref.startswith("git+"):
        return None

    from orchestrator_next.paths import pack_root

    candidates = [skill_dir / ref, pack_root() / ref]
    for candidate in candidates:
        base_dir = candidate.resolve()
        for name in ("SKILL.md", "prompt.md"):
            base = base_dir / name
            if base.is_file():
                return (
                    f"Base role: read {base} first (follow its own `extends`, if any) — "
                    "it defines the role this skill specializes.\n\n"
                )
    return None


def _load_prompt_file(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if path.name == "SKILL.md":
        body = strip_frontmatter(raw)
        ref = _extends_ref(raw)
        if ref:
            line = _base_role_line(path.parent, ref)
            if line:
                return line + body
        return body
    return raw


def _append_learnings(prompt_dir: str | Path, instruction: str) -> str:
    """Append colocated ``learnings.md`` beside the prompt that ran (not pack/)."""
    learnings_path = Path(prompt_dir) / "learnings.md"
    if learnings_path.is_file():
        learnings = learnings_path.read_text(encoding="utf-8").strip()
        if learnings:
            return f"{instruction}\n\n{learnings}\n"
    return instruction


def _resolve_local_prompt(contract_dir: str, prompt_ref: str) -> Path | None:
    """Resolve ``prompt:`` relative to the step dir when the file exists there.

    Allows contracts to name a charter inside the step folder (e.g.
    ``explore/SKILL.md`` via a symlink to ``skills/explore`` at the pack root).
    Rejects absolute paths and ``..`` escapes — those stay on the skills
    search path.
    """
    rel = Path(prompt_ref.strip())
    if not prompt_ref.strip() or rel.is_absolute() or ".." in rel.parts:
        return None
    if rel.suffix != ".md":
        return None
    candidate = Path(contract_dir) / rel
    if candidate.is_file():
        return candidate.resolve()
    return None


def _resolve_agent_instruction(
    contract_dir: str, step_id: str, data: dict[str, Any]
) -> tuple[str, str]:
    """Load instruction and resolved prompt dir from ``prompt:``.

    Returns ``(instruction, prompt_dir)``. ``skill:`` is rejected — use ``prompt:``.
    """
    if data.get("skill"):
        raise ContractError(
            f"step contract {step_id} uses removed skill: field; "
            f"use prompt: <path>.md (resolved via prompt search dirs)"
        )
    prompt = data.get("prompt")
    if not prompt:
        # Colocated fallback: prompt file beside the step contract.
        for rel in ("pack/SKILL.md", "SKILL.md", "pack/prompt.md", "prompt.md"):
            path = Path(contract_dir) / rel
            if path.is_file():
                prompt_dir = path.parent
                instruction = _append_learnings(prompt_dir, _load_prompt_file(path))
                return instruction, str(prompt_dir.resolve())
        raise ContractError(
            f"step contract {step_id} must declare prompt: <path>.md "
            "(or run: for shell steps)"
        )

    if data.get("model") is not None:
        raise ContractError(
            f"step contract {step_id}: model: is removed — map the step under "
            f"step_models: in models.yaml instead"
        )
    if not isinstance(prompt, str) or not prompt.strip():
        raise ContractError(f"step contract {step_id} prompt: must be a non-empty string")

    # Step-local path wins (<id>/SKILL.md symlink layout); else skills search.
    prompt_file = _resolve_local_prompt(contract_dir, prompt)
    if prompt_file is None:
        prompt_file = resolve_prompt_file(prompt)
    prompt_dir = prompt_file.parent
    instruction = _append_learnings(
        prompt_dir,
        _load_prompt_file(prompt_file),
    )
    return instruction, str(prompt_dir)


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
    # <repo>/.orchestrator/config — see paths.config_root).
    from orchestrator_next.paths import config_root
    dirs.append(str(config_root() / "steps"))

    return dirs



def _make_contract(
    step_id: str,
    data: dict[str, Any],
    run: str | None,
    instruction: str,
    prompt_dir: str | None = None,
) -> StepContract:
    shared = dict(
        id=data.get("id", step_id),
        state_mutating=bool(data.get("state_mutating", False)),
        await_input=bool(data.get("await_input", False)),
    )
    if run is None:
        raw_defaults = data.get("default_outputs")
        default_outputs = raw_defaults if isinstance(raw_defaults, dict) else {}
        raw_req = data.get("required_outputs_for_completed")
        req: list = []
        if isinstance(raw_req, list):
            for entry in raw_req:
                if isinstance(entry, dict) and "key" in entry and "value" in entry:
                    req.append({"key": str(entry["key"]), "value": entry["value"]})
                else:
                    import sys as _sys
                    _sys.stderr.write(
                        f"[parser] malformed required_outputs_for_completed entry skipped: {entry!r}\n"
                    )
        return AgentStepContract(
            **shared,
            model=data.get("model") or None,
            instruction=instruction,
            prompt_dir=prompt_dir,
            default_outputs=default_outputs,
            required_outputs_for_completed=req,
        )
    return ScriptStepContract(**shared, run=run)


def load_contract_for_step(step_id: str) -> StepContract:
    """Load and parse a step contract YAML.

    Searches each configured directory for <id>/contract.yaml (directory form).
    """
    search_dirs = _contract_search_dirs()
    for d in search_dirs:
        dir_contract = os.path.join(d, step_id, "contract.yaml")
        if os.path.isfile(dir_contract):
            contract_dir = os.path.join(d, step_id)
            with open(dir_contract, "r") as f:
                data = yaml.safe_load(f)

            is_script = bool(data.get("run"))
            if is_script:
                if data.get("skill") or data.get("prompt"):
                    raise ContractError(
                        f"step contract {step_id} with run: must not declare skill: or prompt:"
                    )
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
                prompt_dir = None
            else:
                instruction, prompt_dir = _resolve_agent_instruction(
                    contract_dir, step_id, data
                )
                run = None

            return _make_contract(
                step_id, data, run, instruction, prompt_dir=prompt_dir
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
        agent=raw.get("agent"),
        attempt=raw.get("attempt"),
        started_at=raw.get("started_at"),
        ended_at=str(ended_at) if ended_at is not None else None,
        usage=raw.get("usage", {}),
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
