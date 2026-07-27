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
    state_mutating: bool = False
    default_outputs: dict = field(default_factory=dict)


@dataclass
class ScriptStepContract:
    """Contract for steps executed as inline scripts."""
    id: str
    run: str
    # When true, the driver records the step BEFORE running the script so
    # state.yaml is consistent even if the script moves or rewrites it.
    state_mutating: bool = False


StepContract = AgentStepContract | ScriptStepContract


_FRONTMATTER_DELIM = "---"


def strip_skill_frontmatter(text: str) -> str:
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


def _repo_root_from_config() -> Path | None:
    """Best-effort checkout root when ORCHESTRATOR_CONFIG points at <repo>/config."""
    try:
        from orchestrator_next.paths import config_root

        root = config_root()
    except Exception:
        return None
    if root.name == "config":
        return root.parent
    return None


def skill_search_dirs() -> list[Path]:
    """Ordered dirs that may contain installable skills (<name>/SKILL.md)."""
    dirs: list[Path] = []
    override = os.environ.get("ORCHESTRATOR_SKILLS_TEST_OVERRIDE")
    if override:
        dirs.append(Path(override))
        return dirs

    repo = _repo_root_from_config()
    if repo is not None:
        dirs.append(repo / "skills")

    # Engine checkout (when installed as a package, this is the wheel data root's sibling).
    here = Path(__file__).resolve().parent.parent
    skills_here = here / "skills"
    if skills_here not in dirs:
        dirs.append(skills_here)

    home = Path.home()
    for extra in (
        home / ".claude" / "skills",
        home / ".codex" / "skills",
        home / ".agents" / "skills",
        Path(os.environ.get("PI_CODING_AGENT_DIR", str(home / ".pi" / "agent"))) / "skills",
    ):
        if extra not in dirs:
            dirs.append(extra)
    return dirs


def resolve_skill_path(skill_name: str) -> Path:
    """Return path to SKILL.md for an installed skill, or raise ContractError."""
    for root in skill_search_dirs():
        candidate = root / skill_name / "SKILL.md"
        if candidate.is_file():
            return candidate
        # Install may link the pack dir (SKILL.md at link root).
        linked = root / skill_name
        if linked.is_dir() and (linked / "SKILL.md").is_file():
            return linked / "SKILL.md"
    raise ContractError(
        f"skill {skill_name!r} not found (searched: "
        + ", ".join(str(d) for d in skill_search_dirs())
        + ")"
    )


def _load_prompt_file(path: Path, *, strip_frontmatter: bool) -> str:
    raw = path.read_text(encoding="utf-8")
    if strip_frontmatter or path.name == "SKILL.md":
        return strip_skill_frontmatter(raw)
    return raw


def _append_learnings(contract_dir: str, instruction: str) -> str:
    learnings_path = os.path.join(contract_dir, "pack", "learnings.md")
    if not os.path.isfile(learnings_path):
        learnings_path = os.path.join(contract_dir, "learnings.md")
    if os.path.isfile(learnings_path):
        with open(learnings_path, "r", encoding="utf-8") as f:
            learnings = f.read().strip()
        if learnings:
            return f"{instruction}\n\n{learnings}\n"
    return instruction


def _resolve_agent_instruction(contract_dir: str, step_id: str, data: dict[str, Any]) -> str:
    """Load instruction from ``skill:`` or ``prompt:`` (exactly one required)."""
    skill = data.get("skill")
    prompt = data.get("prompt")
    if skill and prompt:
        raise ContractError(
            f"step contract {step_id} must not declare both skill: and prompt:"
        )
    if not skill and not prompt:
        # Legacy fallback: pack/SKILL.md or pack/prompt.md (pre-skill:/prompt: contracts).
        for rel in ("pack/SKILL.md", "SKILL.md", "pack/prompt.md", "prompt.md"):
            path = Path(contract_dir) / rel
            if path.is_file():
                return _append_learnings(
                    contract_dir,
                    _load_prompt_file(path, strip_frontmatter=rel.endswith("SKILL.md")),
                )
        raise ContractError(
            f"step contract {step_id} must declare skill: <name> or prompt: <file> "
            "(or run: for shell steps)"
        )

    if not data.get("model"):
        raise ContractError(
            f"step contract {step_id} with skill:/prompt: requires model: <alias>"
        )

    if skill:
        if not isinstance(skill, str) or not skill.strip():
            raise ContractError(f"step contract {step_id} skill: must be a non-empty string")
        path = resolve_skill_path(skill.strip())
        return _append_learnings(
            contract_dir, _load_prompt_file(path, strip_frontmatter=True)
        )

    if not isinstance(prompt, str) or not prompt.strip():
        raise ContractError(f"step contract {step_id} prompt: must be a non-empty string")
    rel = Path(prompt.strip())
    if rel.is_absolute() or ".." in rel.parts:
        raise ContractError(
            f"step contract {step_id} prompt: must be a relative path within the step dir"
        )
    path = Path(contract_dir) / rel
    if not path.is_file():
        raise ContractError(
            f"step contract {step_id} prompt file missing: {path}"
        )
    return _append_learnings(
        contract_dir, _load_prompt_file(path, strip_frontmatter=path.name == "SKILL.md")
    )


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
) -> StepContract:
    shared = dict(
        id=data.get("id", step_id),
        state_mutating=bool(data.get("state_mutating", False)),
    )
    if run is None:
        raw_defaults = data.get("default_outputs")
        default_outputs = raw_defaults if isinstance(raw_defaults, dict) else {}
        return AgentStepContract(
            **shared,
            model=data.get("model") or None,
            instruction=instruction,
            default_outputs=default_outputs,
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
            else:
                instruction = _resolve_agent_instruction(contract_dir, step_id, data)
                run = None

            return _make_contract(step_id, data, run, instruction)

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
