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


# --- protocol v2 step kinds (docs/protocol-v2.md §3) ------------------------
KIND_EXEC = "exec"
KIND_JUDGMENT = "judgment"
KIND_GATE = "gate"
VALID_KINDS = frozenset({KIND_EXEC, KIND_JUDGMENT, KIND_GATE})

# Protocol v1 spelled the (decorative) kind `agent` / `script`. v2 renames them
# to judgment / exec and makes them load-bearing; the old spellings stay
# readable so a v1 pack doesn't fail to load mid-migration.
_KIND_ALIASES = {"agent": KIND_JUDGMENT, "script": KIND_EXEC}


@dataclass
class AgentStepContract:
    """Contract for steps dispatched to an agent subprocess."""
    id: str
    instruction: str
    model: str | None = None
    # Resolved prompt directory (skills/<name> or legacy step dir). Exported as
    # ORCHESTRATOR_PROMPT_DIR so learn can colocate scenarios beside the charter.
    prompt_dir: str | None = None
    state_mutating: bool = False
    # --- protocol v2 (Phase 1.2) ---
    kind: str = KIND_JUDGMENT
    max_turns: int | None = None
    tools: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)   # contract `in:`
    outputs: dict[str, dict] = field(default_factory=dict)  # contract `out:`
    validate: str = ""  # shell script run after out: artifacts land
    # Names from this step's in:/out: whose VALUES must never reach the run
    # doc. record.py redacts them out of the history entry; artifact paths
    # and hashes survive (see redact.py).
    pii: list[str] = field(default_factory=list)


@dataclass
class ScriptStepContract:
    """Contract for steps executed as inline scripts."""
    id: str
    run: str
    # When true, the driver records the step BEFORE running the script so
    # state.yaml is consistent even if the script moves or rewrites it.
    state_mutating: bool = False
    # --- protocol v2 (Phase 1.2) ---
    kind: str = KIND_EXEC
    tools: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)
    outputs: dict[str, dict] = field(default_factory=dict)
    validate: str = ""  # shell script run after out: artifacts land
    # Names from this step's in:/out: whose VALUES must never reach the run
    # doc. record.py redacts them out of the history entry; artifact paths
    # and hashes survive (see redact.py).
    pii: list[str] = field(default_factory=list)


@dataclass
class GateStepContract:
    """Contract for a signoff gate (protocol v2 §7).

    Phase 1.2 parses and validates it; dispatching one reports
    ``status: blocked`` / ``kind: gate``. Tokens arrive in Phase 3.
    """
    id: str
    kind: str = KIND_GATE
    state_mutating: bool = False
    show: list[str] = field(default_factory=list)
    approve_as: str = ""
    tools: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)
    outputs: dict[str, dict] = field(default_factory=dict)
    validate: str = ""  # shell script run after out: artifacts land
    # Names from this step's in:/out: whose VALUES must never reach the run
    # doc. record.py redacts them out of the history entry; artifact paths
    # and hashes survive (see redact.py).
    pii: list[str] = field(default_factory=list)


StepContract = AgentStepContract | ScriptStepContract | GateStepContract


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

    Returns ``(instruction, prompt_dir)``.
    """
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



# ---------------------------------------------------------------------------
# protocol v2 contract keys (Phase 1.2)
# ---------------------------------------------------------------------------
def _str_list(step_id: str, key: str, value: Any) -> list[str]:
    """Coerce a contract list-of-strings key, rejecting anything else."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ContractError(
            f"step contract {step_id}: {key}: must be a list of strings (got {value!r})"
        )
    return list(value)


def _parse_io_map(step_id: str, key: str, value: Any) -> dict[str, dict]:
    """Parse a contract ``in:`` / ``out:`` block into ``{name: spec}``.

    Each entry is a mapping declaring either ``artifact:`` (a file the step
    reads or writes) or ``type:`` (a scalar value carried in the done payload).
    Malformed entries are a hard ContractError — protocol v2 §6 makes these
    load-bearing, so silently ignoring a typo would drop validation.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(
            f"step contract {step_id}: {key}: must be a mapping of name -> spec"
        )
    parsed: dict[str, dict] = {}
    for name, spec in value.items():
        if not isinstance(spec, dict):
            raise ContractError(
                f"step contract {step_id}: {key}.{name} must be a mapping "
                f"(e.g. {{artifact: design.md}} or {{type: enum, values: [...]}})"
            )
        if "artifact" not in spec and "type" not in spec:
            raise ContractError(
                f"step contract {step_id}: {key}.{name} must declare artifact: or type:"
            )
        if "artifact" in spec and not isinstance(spec["artifact"], str):
            raise ContractError(
                f"step contract {step_id}: {key}.{name}.artifact must be a string"
            )
        if spec.get("type") == "enum" and not isinstance(spec.get("values"), list):
            raise ContractError(
                f"step contract {step_id}: {key}.{name} type: enum requires values: [...]"
            )
        parsed[str(name)] = dict(spec)
    return parsed


def _resolve_kind(step_id: str, data: dict[str, Any], run: str | None) -> str:
    """Return the validated step kind, inferring it when absent.

    Inference (protocol v2 §10 migration): ``run:`` -> exec, ``prompt:`` ->
    judgment. An explicit ``kind:`` must match that shape, except ``gate``
    which stands alone.
    """
    raw = data.get("kind")
    inferred = KIND_EXEC if run is not None else KIND_JUDGMENT
    if raw is None:
        return inferred
    if isinstance(raw, str):
        raw = _KIND_ALIASES.get(raw, raw)
    if not isinstance(raw, str) or raw not in VALID_KINDS:
        raise ContractError(
            f"step contract {step_id}: unknown kind: {raw!r} "
            f"(expected one of {', '.join(sorted(VALID_KINDS))})"
        )
    if raw != KIND_GATE and raw != inferred:
        raise ContractError(
            f"step contract {step_id}: kind: {raw!r} conflicts with the step's "
            f"payload ({'run:' if run is not None else 'prompt:'} implies {inferred!r})"
        )
    return raw


def _v2_fields(step_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """The protocol-v2 keys shared by every contract kind."""
    return {
        "tools": _str_list(step_id, "tools", data.get("tools")),
        "side_effects": _str_list(step_id, "side_effects", data.get("side_effects")),
        "inputs": _parse_io_map(step_id, "in", data.get("in")),
        "outputs": _parse_io_map(step_id, "out", data.get("out")),
        "validate": _validate_script(step_id, data.get("validate")),
        "pii": _str_list(step_id, "pii", data.get("pii")),
    }


def _validate_script(step_id: str, value: Any) -> str:
    """Parse a contract's optional ``validate:`` shell script."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ContractError(
            f"step contract {step_id}: validate: must be a shell command string"
        )
    return value


def _make_contract(
    step_id: str,
    data: dict[str, Any],
    run: str | None,
    instruction: str,
    prompt_dir: str | None = None,
) -> StepContract:
    kind = _resolve_kind(step_id, data, run)
    v2 = _v2_fields(step_id, data)

    if kind == KIND_GATE:
        return GateStepContract(
            id=data.get("id", step_id),
            state_mutating=bool(data.get("state_mutating", False)),
            show=_str_list(step_id, "show", data.get("show")),
            approve_as=str(data.get("approve_as") or ""),
            **v2,
        )

    shared = dict(
        id=data.get("id", step_id),
        state_mutating=bool(data.get("state_mutating", False)),
        kind=kind,
        **v2,
    )
    if run is None:
        raw_turns = data.get("max_turns")
        if raw_turns is not None and (not isinstance(raw_turns, int) or isinstance(raw_turns, bool) or raw_turns < 1):
            raise ContractError(
                f"step contract {step_id}: max_turns: must be a positive integer "
                f"(got {raw_turns!r})"
            )
        return AgentStepContract(
            **shared,
            instruction=instruction,
            prompt_dir=prompt_dir,
            max_turns=raw_turns,
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

            if not isinstance(data, dict):
                raise ContractError(
                    f"step contract {step_id}: contract.yaml must be a YAML mapping"
                )

            # A gate has neither a script nor a prompt payload — it is pure
            # metadata the engine renders for a human (protocol v2 §7).
            if data.get("kind") == KIND_GATE:
                return _make_contract(step_id, data, None, "", prompt_dir=None)

            is_script = bool(data.get("run"))
            if is_script:
                if data.get("prompt"):
                    raise ContractError(
                        f"step contract {step_id} with run: must not declare prompt:"
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


@dataclass
class Recipe:
    """A workflow YAML: its steps plus the Phase 2.1 run-level declarations."""
    name: str
    steps: list
    artifacts_root: str = ""          # template, e.g. "spec/changes/{slug}"
    inputs: dict[str, dict] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def load_recipe(schema_name: str, repo_root: str | Path = "") -> Recipe:
    """Load ``<config>/workflows/<name>.yaml`` into a Recipe.

    ``artifacts_root`` and ``inputs`` are the two Phase 2.1 additions; both are
    optional, so an unmigrated recipe loads unchanged.

    ``repo_root`` is the run's own, from state. Without it the pack is resolved
    from the ambient cwd/env, which is wrong for every caller that already
    knows which run it is acting on: a step executing inside a worktree has no
    pack under its cwd, so the lookup either failed or silently found some
    *other* repo's recipe of the same name and dropped its ``artifacts_root``.
    Resolution mirrors ``seed._schema_active_steps``.
    """
    from orchestrator_next.paths import (
        WorkflowRefError,
        config_root,
        resolve_workflow_ref,
    )

    path: Path | None = None
    if repo_root:
        try:
            _pack, wf, cfg = resolve_workflow_ref(schema_name, Path(repo_root))
            candidate = cfg / "workflows" / f"{wf}.yaml"
            if candidate.is_file():
                path = candidate
        except (WorkflowRefError, OSError):
            path = None
    if path is None:
        path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ContractError(f"workflow {schema_name}: top level must be a mapping")

    root = doc.get("artifacts_root") or ""
    if root and not isinstance(root, str):
        raise ContractError(f"workflow {schema_name}: artifacts_root must be a string")

    raw_inputs = doc.get("inputs") or {}
    if not isinstance(raw_inputs, dict):
        raise ContractError(f"workflow {schema_name}: inputs must be a mapping")
    inputs: dict[str, dict] = {}
    for name, spec in raw_inputs.items():
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise ContractError(
                f"workflow {schema_name}: inputs.{name} must be a mapping"
            )
        inputs[str(name)] = dict(spec)

    return Recipe(
        name=schema_name,
        steps=list(doc.get("steps") or []),
        artifacts_root=str(root),
        inputs=inputs,
        raw=doc,
    )


def load_state(state_yaml_path: str) -> State:
    """
    Parse state.yaml at the given path and return a State object.

    Does NOT load step contracts — those are loaded on demand by dispatch.py.
    """
    # `state_yaml_path` is a HANDLE, not necessarily a path: a bare path or
    # file:// URL reads the YAML file exactly as before, while sqlite:// and
    # postgresql:// read the run out of a store. See state_store.py.
    from orchestrator_next import state_store

    handle = state_store.parse_handle(state_yaml_path)
    if handle.is_file:
        path = Path(handle.location).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"state.yaml not found: {state_yaml_path}")
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
    else:
        raw, _token, _h = state_store.load_doc(handle)

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
