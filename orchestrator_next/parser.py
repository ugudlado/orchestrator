"""Parse step contracts.

A step contract is ``<config_root>/steps/<step_id>/contract.yaml``: what the
step declares it reads (``in:``), writes (``out:``), and how it runs —
``run: script.sh`` for an exec step, ``prompt: SKILL.md`` for a judgment step.

The config root is always passed in. Nothing here reads the environment or
any run state; there is no run state.
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
    # Absolute path to the charter the driver reads (e.g. <step>/SKILL.md).
    # The engine resolves the path and never opens the file: composing the
    # prompt (frontmatter, `extends:` base roles, learnings) is the driver's.
    prompt_path: str = ""
    # Directory holding the charter, so colocated files (scenarios, learnings)
    # are reachable without re-deriving it.
    prompt_dir: str | None = None
    state_mutating: bool = False
    # --- protocol v2 (Phase 1.2) ---
    kind: str = KIND_JUDGMENT
    max_turns: int | None = None
    tools: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)  # contract `in:`
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

    # Parsed at load time, without changing the existing constructor.
    params: dict[str, str] = field(default_factory=dict, init=False)


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


def prompt_search_dirs(config_root: Path) -> list[Path]:
    """Dirs searched to resolve ``prompt:`` refs (e.g. ``<name>/SKILL.md``).

    Just the pack's own ``skills/`` dir, beside the config root. Skills live
    beside ``config/``, never inside it.
    """
    return [Path(config_root).parent / "skills"]


def resolve_prompt_file(prompt_ref: str, config_root: Path) -> Path:
    """Return the prompt ``.md`` file resolved through ``prompt_search_dirs()``.

    ``prompt:`` is a relative path to a markdown file: ``<name>/SKILL.md``
    (skill conventions — frontmatter stripped, colocated scenarios/learnings)
    or any other ``.md`` file (loaded verbatim). Directory names are rejected;
    the contract names the file itself.
    """
    ref = prompt_ref.strip()
    rel = Path(ref)
    if not ref or rel.is_absolute() or ".." in rel.parts:
        raise ContractError(f"prompt: must be a relative .md path (got {prompt_ref!r})")
    if rel.suffix != ".md":
        raise ContractError(
            f"prompt: must point at a .md file, e.g. {ref}/SKILL.md or {ref}/prompt.md (got {prompt_ref!r})"
        )
    searched = prompt_search_dirs(config_root)
    for root in searched:
        candidate = root / rel
        if candidate.is_file():
            return candidate.resolve()
    raise ContractError(f"prompt {ref!r} not found (searched: " + ", ".join(str(d) for d in searched) + ")")


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


def _resolve_prompt_path(contract_dir: str, step_id: str, data: dict[str, Any], config_root: Path) -> tuple[str, str]:
    """Resolve ``prompt:`` to ``(prompt_path, prompt_dir)``.

    Path resolution only — the file is never opened. The driver reads the
    charter and decides what to do with its frontmatter, ``extends:`` chain
    and any colocated learnings.
    """
    prompt = data.get("prompt")
    if not prompt:
        # Colocated fallback: charter beside the step contract.
        for rel in ("pack/SKILL.md", "SKILL.md", "pack/prompt.md", "prompt.md"):
            path = Path(contract_dir) / rel
            if path.is_file():
                resolved = path.resolve()
                return str(resolved), str(resolved.parent)
        raise ContractError(f"step contract {step_id} must declare prompt: <path>.md (or run: for shell steps)")

    if not isinstance(prompt, str) or not prompt.strip():
        raise ContractError(f"step contract {step_id} prompt: must be a non-empty string")

    # Step-local path wins (<id>/SKILL.md symlink layout); else skills search.
    prompt_file = _resolve_local_prompt(contract_dir, prompt)
    if prompt_file is None:
        prompt_file = resolve_prompt_file(prompt, config_root)
    return str(prompt_file), str(prompt_file.parent)


# ---------------------------------------------------------------------------
# protocol v2 contract keys (Phase 1.2)
# ---------------------------------------------------------------------------
def _str_list(step_id: str, key: str, value: Any) -> list[str]:
    """Coerce a contract list-of-strings key, rejecting anything else."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ContractError(f"step contract {step_id}: {key}: must be a list of strings (got {value!r})")
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
        raise ContractError(f"step contract {step_id}: {key}: must be a mapping of name -> spec")
    parsed: dict[str, dict] = {}
    for name, spec in value.items():
        if not isinstance(spec, dict):
            raise ContractError(
                f"step contract {step_id}: {key}.{name} must be a mapping "
                f"(e.g. {{artifact: design.md}} or {{type: enum, values: [...]}})"
            )
        if "artifact" not in spec and "type" not in spec:
            raise ContractError(f"step contract {step_id}: {key}.{name} must declare artifact: or type:")
        if "artifact" in spec and not isinstance(spec["artifact"], str):
            raise ContractError(f"step contract {step_id}: {key}.{name}.artifact must be a string")
        if spec.get("type") == "enum" and not isinstance(spec.get("values"), list):
            raise ContractError(f"step contract {step_id}: {key}.{name} type: enum requires values: [...]")
        if "fail_on" in spec:
            # `fail_on:` names the enum values that mean the step's own
            # judgment was negative (a review verdict of needs_work). Routing
            # treats them as a failure and takes the node's on_failure edge, so
            # they must be values the step can actually report.
            if spec.get("type") != "enum":
                raise ContractError(f"step contract {step_id}: {key}.{name} fail_on: requires type: enum")
            if not isinstance(spec["fail_on"], list):
                raise ContractError(f"step contract {step_id}: {key}.{name} fail_on: must be a list")
            unknown = [v for v in spec["fail_on"] if v not in (spec.get("values") or [])]
            if unknown:
                raise ContractError(
                    f"step contract {step_id}: {key}.{name} fail_on: {unknown} not in values: {spec.get('values')}"
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
            f"step contract {step_id}: unknown kind: {raw!r} (expected one of {', '.join(sorted(VALID_KINDS))})"
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
        raise ContractError(f"step contract {step_id}: validate: must be a shell command string")
    return value


def _make_contract(
    step_id: str,
    data: dict[str, Any],
    run: str | None,
    prompt_path: str = "",
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
            raise ContractError(f"step contract {step_id}: max_turns: must be a positive integer (got {raw_turns!r})")
        return AgentStepContract(
            **shared,
            prompt_path=prompt_path,
            prompt_dir=prompt_dir,
            max_turns=raw_turns,
        )
    contract = ScriptStepContract(**shared, run=run)
    params = data.get("params")
    if isinstance(params, dict):
        contract.params = {str(k): str(v) for k, v in params.items()}
    return contract


def load_contract_for_step(step_id: str, config_root: Path) -> StepContract:
    """Load and parse ``<config_root>/steps/<step_id>/contract.yaml``."""
    steps_dir = Path(config_root) / "steps"
    contract_dir = os.path.join(str(steps_dir), step_id)
    path = Path(contract_dir) / "contract.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Step contract not found for '{step_id}'. Searched: {[str(steps_dir)]}")
    with path.open("r") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ContractError(f"step contract {step_id}: contract.yaml must be a YAML mapping")

    # A gate has neither a script nor a prompt payload — it is pure
    # metadata the engine renders for a human (protocol v2 §7).
    if data.get("kind") == KIND_GATE:
        return _make_contract(step_id, data, None, "", prompt_dir=None)

    if data.get("run"):
        if data.get("prompt"):
            raise ContractError(f"step contract {step_id} with run: must not declare prompt:")
        run_rel = data.get("run")
        run = run_rel if os.path.isabs(run_rel) else os.path.join(contract_dir, run_rel)
        if not os.path.isfile(run):
            raise ContractNotFoundError(f"script contract {step_id} missing script payload: {run}")
        prompt_path = ""
        prompt_dir = None
    else:
        prompt_path, prompt_dir = _resolve_prompt_path(contract_dir, step_id, data, config_root)
        run = None

    return _make_contract(step_id, data, run, prompt_path, prompt_dir=prompt_dir)
