"""`orchestrator next` — the whole engine.

Given a workflow, the step that just ran, and how it went, say what runs
next. That is the entire contract. The engine keeps no state: no history, no
attempt counters, no gate tokens, no run documents. The driver owns all of
that and passes back whatever this function needs to decide.

Routing, in full:

  no ``--after``        → the workflow's first step
  completed             → the following entry (``route: next``); past the end
                          → ``{"status": "done"}``
  failed                → ``outputs.reset_to`` when the step named one and it
                          points at or before itself (``route: reset_to``),
                          else the entry's ``on_failure`` target
                          (``route: on_failure``); when ``--attempt`` has
                          reached ``max_retries`` → ``needs_you``
  abandoned             → the same step again (``route: retry``)

A judgment step whose contract marks an out value ``fail_on:`` is routed as
``failed`` even when the driver reports it ``completed`` — a review that says
``needs_work`` must not advance onto the work it just rejected.

Attempts, precisely. ``--attempt N`` is **how many times the step being
reported has run in this whole run, counting this one**. It is a per-step
lifetime counter, never reset by routing: a review that fails, sends the run
back to an earlier step, and is then reached again reports ``--attempt 2``
the second time, even though the run "arrived" at it afresh. The cap is
checked against the *failing* step's own counter (the step named by
``--after``), not the ``on_failure`` target's — the same step whose
``max_retries`` bounds it.

The answer carries no ``attempt``: the engine has no history, so any number
it echoed would be a guess. A driver that trusted such a guess over its own
history would reset the counter on every forward move and never exhaust a
retry cap.

A gate is emitted as a ready step with its ``show:`` artifacts resolved; the
driver approves it however it likes and calls back with
``--after <gate> --status completed``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from orchestrator_next.parser import (
    KIND_EXEC,
    KIND_GATE,
    KIND_JUDGMENT,
    AgentStepContract,
    ContractError,
    ContractNotFoundError,
    GateStepContract,
    ScriptStepContract,
    load_contract_for_step,
    prompt_search_dirs,
)
from orchestrator_next.workflow_steps import is_gate_entry, normalize_step_entry

#: Retries allowed on an ``on_failure`` back-edge when the entry names no
#: ``max_retries:``. Matches the engine's historical default.
DEFAULT_MAX_RETRIES = 3

#: Terminal step statuses a driver may report.
VALID_STATUSES = ("completed", "failed", "abandoned")


class NextError(RuntimeError):
    """The call could not be served: bad workflow, unknown step, bad flags."""


# ---------------------------------------------------------------------------
# workflow loading
# ---------------------------------------------------------------------------
def load_workflow(workflow: str, config_root: Path) -> dict[str, Any]:
    """Load ``<config_root>/workflows/<workflow>.yaml``."""
    name = (workflow or "").strip()
    if not name or "/" in name or ".." in name:
        raise NextError(f"workflow must be a bare name (got {workflow!r})")
    path = config_root / "workflows" / f"{name}.yaml"
    if not path.is_file():
        available = sorted(
            p.stem for p in (config_root / "workflows").glob("*.yaml")
        ) if (config_root / "workflows").is_dir() else []
        raise NextError(
            f"unknown workflow {name!r} in {config_root / 'workflows'} "
            f"(available: {', '.join(available) or 'none'})"
        )
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise NextError(f"{path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise NextError(f"{path}: workflow must be a YAML mapping")
    return doc


def step_entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The workflow's steps, normalized to dicts carrying at least ``id``."""
    raw = doc.get("steps")
    if not isinstance(raw, list):
        raise NextError("workflow has no steps: list")
    out = []
    for entry in raw:
        norm = normalize_step_entry(entry)
        if norm.get("id"):
            norm["_gate"] = is_gate_entry(entry)
            out.append(norm)
    if not out:
        raise NextError("workflow has no usable steps")
    return out


def _index_of(entries: list[dict[str, Any]], step_id: str) -> int:
    for i, entry in enumerate(entries):
        if entry["id"] == step_id:
            return i
    raise NextError(f"step {step_id!r} is not in this workflow")


# ---------------------------------------------------------------------------
# artifact paths
# ---------------------------------------------------------------------------
def artifacts_base(doc: dict[str, Any], slug: str) -> PurePosixPath:
    """Where this run's named artifacts live, RELATIVE to the working dir.

    The workflow's ``artifacts_root`` template with ``{slug}`` filled in, or
    the engine's default location when the workflow declares none.

    Relative on purpose. A run's artifacts live in its worktree, and the
    worktree is the driver's — it created it and knows its path. An absolute
    path rendered here against some "repo root" would be wrong the moment a
    worktree exists, so the engine says *where under the working dir* and the
    driver joins that to the tree it is actually running in.
    """
    template = str(doc.get("artifacts_root") or "").strip()
    if template:
        if "{slug}" in template and not slug:
            raise NextError(
                f"workflow artifacts_root is {template!r} but no --slug was given"
            )
        return PurePosixPath(template.format(slug=slug))
    if not slug:
        raise NextError("no --slug: artifact paths cannot be resolved without one")
    return PurePosixPath(".orchestrator") / "runs" / slug / "artifacts"


def _resolve_io(
    specs: dict[str, dict], base: PurePosixPath
) -> tuple[dict[str, str], dict[str, dict]]:
    """Split an ``in:``/``out:`` block into paths and a value schema.

    Paths are relative to the driver's working dir (see ``artifacts_base``).
    """
    paths: dict[str, str] = {}
    schema: dict[str, dict] = {}
    for name, spec in (specs or {}).items():
        artifact = spec.get("artifact")
        if artifact:
            paths[name] = str(base / str(artifact))
        else:
            # `fail_on` rides along so a reader can see which values the
            # contract treats as a rejection — the engine derives routing
            # from it, but a driver should be able to see the same rule.
            schema[name] = {k: v for k, v in spec.items() if k != "artifact"}
    return paths, schema


def _show_paths(
    entries: list[dict[str, Any]],
    show: list[str],
    base: PurePosixPath,
    config_root: Path,
) -> dict[str, str]:
    """Resolve a gate's ``show:`` names to paths, by asking who declares them.

    A name is whatever an upstream step declared under ``out:``; the last
    declaring step wins, which is the one a reviewer wants to see.
    """
    wanted, found = set(show or []), {}
    for entry in entries:
        try:
            contract = load_contract_for_step(entry["id"], config_root)
        except (OSError, ValueError):
            # A gate preview is best-effort: a step whose contract will not
            # load simply contributes no paths.
            continue
        for name, spec in (getattr(contract, "outputs", None) or {}).items():
            if name in wanted and spec.get("artifact"):
                found[name] = str(base / str(spec["artifact"]))
    return found


# ---------------------------------------------------------------------------
# the step payload
# ---------------------------------------------------------------------------
def failing_verdict(contract: Any, out: dict[str, Any] | None) -> str:
    """The contract-declared negative verdict this payload reported, or "".

    A judgment contract may mark enum outs with ``fail_on:``::

        out:
          verdict: {type: enum, values: [pass, needs_work], fail_on: [needs_work]}

    A step reporting one of those values has judged its own subject
    unacceptable. The step itself ran fine — the driver reports it
    ``completed`` — but the workflow must not advance, or the next step
    consumes work the reviewer just rejected. Routing treats it as a failure
    and takes the ``on_failure`` edge, bounded by ``max_retries``.

    Deriving this here rather than in the driver is what keeps a rejected
    review from being waved through by a driver that forgot the rule.
    """
    if not isinstance(out, dict):
        return ""
    for name, spec in (getattr(contract, "outputs", None) or {}).items():
        fail_on = spec.get("fail_on")
        if not isinstance(fail_on, list):
            continue
        if out.get(name) in fail_on:
            return f"{name}={out[name]}"
    return ""


def _prompt_dir_map(entries: list[dict[str, Any]], config_root: Path) -> dict[str, str]:
    """step_id → charter dir for every judgment step in this workflow.

    Config-derived, so the engine can answer it without any run state. The
    learn charter needs it to know where a proposed scenario could land.
    """
    dirs: dict[str, str] = {}
    for entry in entries:
        step_id = entry["id"]
        if step_id in dirs or entry.get("_gate"):
            continue
        try:
            contract = load_contract_for_step(step_id, config_root)
        except (OSError, ValueError):
            continue
        if isinstance(contract, AgentStepContract) and contract.prompt_dir:
            dirs[step_id] = contract.prompt_dir
    return dirs


def _step_env(step_id: str, step_dir: str, slug: str,
              prompt_dirs: dict[str, str] | None = None,
              prompt_path: str = "") -> dict[str, str]:
    """The variables the ENGINE contributes to a step's environment.

    Only what the engine actually knows: which step, which attempt, where the
    step's own files are, and the run's slug. Everything else a script reads
    — the repo root, the worktree, the branch — belongs to the driver, which
    is the thing that created them.

    The caller's environment is deliberately absent: this block is printed as
    part of the payload, and copying os.environ into it would print every
    secret the engine was started with. The driver merges it over its own.
    """
    # No ORCHESTRATOR_ATTEMPT: how many times a step has run is run history,
    # which the driver owns. The engine guessing it is how a retry cap gets
    # silently disarmed.
    env = {"ORCHESTRATOR_STEP_ID": step_id}
    if slug:
        env["ORCHESTRATOR_CHANGE_ID"] = slug
        env["CHANGE_ID"] = slug
    if step_dir:
        env["ORCHESTRATOR_STEP_DIR"] = step_dir
    if prompt_dirs:
        # Every judgment step's charter dir in this workflow. The learn
        # charter reads it to know where a proposed scenario could land.
        env["ORCHESTRATOR_PROMPT_DIRS"] = json.dumps(prompt_dirs, sort_keys=True)
    if prompt_path:
        # The roots persist-learnings confines an append to. Pack-derived, so
        # the engine knows it; without it every proposed row is skipped.
        env["ORCHESTRATOR_PROMPT_PATH"] = prompt_path
    return env


def _contract_params(step_id: str, config_root: Path) -> dict[str, str]:
    """A step contract's ``params:`` block, as environment strings."""
    path = config_root / "steps" / step_id / "contract.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    params = raw.get("params") if isinstance(raw, dict) else None
    if not isinstance(params, dict):
        return {}
    return {str(k): str(v) for k, v in params.items()}


def build_step(
    entry: dict[str, Any],
    doc: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    config_root: Path,
    slug: str,
    route: str,
) -> dict[str, Any]:
    """Build the ready-step answer for one workflow entry."""
    step_id = entry["id"]
    base = artifacts_base(doc, slug)
    prompt_dirs = _prompt_dir_map(entries, config_root)
    prompt_path_env = os.pathsep.join(
        str(d) for d in prompt_search_dirs(config_root)
    )
    result: dict[str, Any] = {
        "status": "ready", "step_id": step_id, "route": route,
    }

    # A gate has no contract file: the workflow entry IS the contract.
    if entry.get("_gate"):
        result["kind"] = KIND_GATE
        result["payload"] = {
            "step_id": step_id,
            "show": _show_paths(entries, entry.get("show") or [], base, config_root),
            "approve_as": str(entry.get("approve_as") or ""),
        }
        return result

    try:
        contract = load_contract_for_step(step_id, config_root)
    except (FileNotFoundError, ContractError, ContractNotFoundError) as exc:
        raise NextError(str(exc)) from exc

    if isinstance(contract, GateStepContract):
        result["kind"] = KIND_GATE
        result["payload"] = {
            "step_id": step_id,
            "show": _show_paths(entries, list(contract.show), base, config_root),
            "approve_as": contract.approve_as,
        }
        return result

    in_paths, _in_schema = _resolve_io(contract.inputs, base)
    out_paths, out_schema = _resolve_io(contract.outputs, base)

    payload: dict[str, Any] = {
        "step_id": step_id,
        "in": in_paths,
        "out": out_paths,
        "out_schema": out_schema,
        "tools": list(contract.tools),
        "side_effects": list(contract.side_effects),
        # Declared by the workflow, echoed as data: with no state to check
        # against, the engine cannot enforce that a token was ever issued.
        # Whoever honours `requires:` is the driver holding the approvals.
        "requires": str(entry.get("requires") or ""),
    }

    if isinstance(contract, ScriptStepContract):
        result["kind"] = KIND_EXEC
        step_dir = str(Path(contract.run).parent)
        payload["run_path"] = contract.run
        payload["step_dir"] = step_dir
        payload["state_mutating"] = bool(contract.state_mutating)
        env = _step_env(step_id, step_dir, slug, prompt_dirs, prompt_path_env)
        for key, value in _contract_params(step_id, config_root).items():
            env.setdefault(key, value)
        payload["env"] = env
    else:
        result["kind"] = KIND_JUDGMENT
        step_dir = contract.prompt_dir or ""
        payload["prompt_path"] = contract.prompt_path
        payload["step_dir"] = step_dir
        payload["max_turns"] = contract.max_turns
        payload["env"] = _step_env(
            step_id, step_dir, slug, prompt_dirs, prompt_path_env
        )

    result["payload"] = payload
    return result


# ---------------------------------------------------------------------------
# the exec stdout protocol
# ---------------------------------------------------------------------------
def parse_script_stdout(stdout: str) -> dict[str, Any]:
    """Parse a script step's stdout into ``{status, outputs, state_patch}``.

    The protocol is the last JSON line of stdout, in either shape:

      ``{"status": "...", "outputs": {...}}``   — explicit status
      ``{"status": "...", "k": v, ...}``        — status plus flat outputs
      ``{"k": v, ...}``                         — flat outputs, status completed

    ``state_patch`` is lifted from the top level or from ``outputs``. The
    engine parses it so every driver does not have to, then hands the pieces
    straight back — it applies nothing, because it stores nothing.
    """
    parsed: Any = {}
    lines = (stdout or "").strip().splitlines()
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except (json.JSONDecodeError, ValueError):
            parsed = {}
    if not isinstance(parsed, dict):
        return {"status": "completed", "outputs": {}, "state_patch": None}

    raw_status, raw_outputs = parsed.get("status"), parsed.get("outputs")
    if isinstance(raw_status, str) and isinstance(raw_outputs, dict):
        status, outputs = raw_status, dict(raw_outputs)
    elif isinstance(raw_status, str):
        status = raw_status
        outputs = {
            k: v for k, v in parsed.items() if k not in ("status", "state_patch")
        }
    else:
        status, outputs = "completed", dict(parsed)

    patch = parsed.get("state_patch")
    if not isinstance(patch, dict):
        patch = outputs.get("state_patch")
    return {
        "status": status,
        "outputs": outputs,
        "state_patch": patch if isinstance(patch, dict) else None,
    }


# ---------------------------------------------------------------------------
# judgment output validation — the trust boundary for agent output
# ---------------------------------------------------------------------------
def validate_out(
    contract: Any, out: dict[str, Any], base: PurePosixPath
) -> list[str]:
    """Every way ``out`` fails the contract's ``out:`` block.

    Artifact outs must exist on disk; ``type: enum`` outs must carry a
    declared value; any other declared out must simply be present. An empty
    list means the payload satisfies the contract.

    A relative path — the engine's own ``out`` paths are relative — resolves
    against the process cwd, which is the worktree the driver invoked the CLI
    from. An absolute path is checked exactly as given.
    """
    declared = getattr(contract, "outputs", None) or {}
    problems: list[str] = []
    for name, spec in declared.items():
        optional = bool(spec.get("optional"))
        artifact = spec.get("artifact")
        if artifact:
            reported = str(out.get(name) or "")
            path = Path(reported) if reported else Path(base / str(artifact))
            if not path.is_absolute():
                path = Path.cwd() / path
            if not path.is_file() and not optional:
                problems.append(f"out.{name}: artifact not found at {path}")
            continue
        if name not in out or out[name] is None:
            if not optional:
                problems.append(
                    f"out.{name}: missing (declared type: {spec.get('type')})"
                )
            continue
        if spec.get("type") == "enum":
            values = spec.get("values") or []
            if out[name] not in values:
                problems.append(f"out.{name}: {out[name]!r} not one of {values}")
    return problems


# ---------------------------------------------------------------------------
# the verb
# ---------------------------------------------------------------------------
def next_step(
    workflow: str,
    *,
    config_root: Path,
    slug: str = "",
    after: str = "",
    status: str = "",
    exit_code: int | None = None,
    stdout_file: str = "",
    out: dict[str, Any] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Answer what runs next. Pure: reads config and disk, writes nothing."""
    doc = load_workflow(workflow, config_root)
    entries = step_entries(doc)
    out = dict(out or {})

    if not after:
        return build_step(
            entries[0], doc, entries, config_root=config_root,
            slug=slug, route="next",
        )

    index = _index_of(entries, after)
    entry = entries[index]
    base = artifacts_base(doc, slug)
    recorded: dict[str, Any] = {}

    # --- derive the outcome ------------------------------------------------
    if exit_code is not None:
        # An exec step: the driver reports what it observed, and the engine
        # parses the stdout protocol so every driver need not reimplement it.
        if exit_code != 0:
            status = "failed"
            recorded = {
                "status": "failed",
                "outputs": {"reason": f"script exited {exit_code}"},
                "state_patch": None,
                "exit_code": exit_code,
            }
        else:
            stdout = ""
            if stdout_file:
                try:
                    stdout = Path(stdout_file).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError as exc:
                    raise NextError(f"--stdout-file unreadable: {exc}") from exc
            parsed = parse_script_stdout(stdout)
            recorded = {**parsed, "exit_code": exit_code}
            if parsed["status"] == "await_input":
                # The step asked a question. Nothing is parked — the engine
                # holds nothing; the driver answers and calls back with
                # `--after` this same step.
                return {
                    "status": "needs_you",
                    "step_id": after,
                    "await_input": parsed["outputs"],
                    "recorded": recorded,
                }
            status = parsed["status"]
            out = {**parsed["outputs"], **out}

    if status not in VALID_STATUSES:
        raise NextError(
            f"--status must be one of {', '.join(VALID_STATUSES)} (got {status!r})"
        )
    recorded.setdefault("status", status)
    recorded.setdefault("outputs", out)

    # --- validate a judgment step's declared output ------------------------
    # The trust boundary: an agent's claim that it produced what the contract
    # asked for is checked against the contract and the filesystem.
    if status == "completed" and not entry.get("_gate") and exit_code is None:
        try:
            contract = load_contract_for_step(after, config_root)
        except (FileNotFoundError, ContractError, ContractNotFoundError):
            contract = None
        if isinstance(contract, AgentStepContract):
            problems = validate_out(contract, out, base)
            if problems:
                return {
                    "status": "error",
                    "step_id": after,
                    "error": "invalid out: " + "; ".join(problems),
                }
            # A contract-declared negative verdict IS a failure, whatever the
            # driver called it. The step ran fine; the work it judged did not
            # pass, so the workflow takes the on_failure edge rather than
            # advancing onto rejected work.
            verdict = failing_verdict(contract, out)
            if verdict:
                status = "failed"
                recorded["status"] = "failed"
                recorded["derived_from"] = f"fail_on ({verdict})"

    def _emit(target_entry, route):
        result = build_step(
            target_entry, doc, entries, config_root=config_root,
            slug=slug, route=route,
        )
        result["recorded"] = recorded
        return result

    # --- route -------------------------------------------------------------
    if status == "abandoned":
        # Re-queue the same step, unchanged: current semantics.
        return _emit(entry, "retry")

    if status == "failed":
        max_retries = int(entry.get("max_retries") or DEFAULT_MAX_RETRIES)
        target_id, route = "", ""

        # A step may name its own rework target, which wins over the static
        # edge — but only at or before itself, so a failure cannot skip work.
        reset_to = str(out.get("reset_to") or "").strip()
        if reset_to:
            ids = [e["id"] for e in entries]
            if reset_to in ids and ids.index(reset_to) <= index:
                target_id, route = reset_to, "reset_to"

        if not target_id and entry.get("on_failure"):
            target_id, route = str(entry["on_failure"]), "on_failure"

        if not target_id:
            return {
                "status": "needs_you", "step_id": after,
                "reason": f"{after} failed and declares no on_failure target",
                "recorded": recorded,
            }
        if attempt >= max_retries:
            return {
                "status": "needs_you", "step_id": after,
                "reason": "retries exhausted",
                "recorded": recorded,
            }
        return _emit(entries[_index_of(entries, target_id)], route)

    # completed
    if index + 1 >= len(entries):
        return {"status": "done", "recorded": recorded}
    return _emit(entries[index + 1], "next")
