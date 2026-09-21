"""CLI verbs: ``start`` / ``step`` / ``done`` / ``status`` (+ gate/resume).

The engine is a step generator and a step recorder. It never spawns a model:
it computes the next step, hands the driver a payload, validates what comes
back, and records it. Metrics, cost, usage and logs belong to the driver.

Every verb in this module exits 0 on success and encodes the run's condition
in the JSON ``status`` field; exit 3 is reserved for an engine error (bad
arguments, unknown run, rejected ``done`` payload).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import (
    AgentStepContract,
    GateStepContract,
    ScriptStepContract,
    KIND_EXEC,
    KIND_GATE,
    KIND_JUDGMENT,
    ContractError,
    ContractNotFoundError,
    compute_attempt,
    load_contract_for_step,
    load_state,
)

# Engine error. Everything else — including a blocked or failed run — is a
# successful call that reports a status.
EXIT_ERROR = 3

# Guards a pathological recipe (or a script step that never advances the DAG)
# from spinning `step` forever while it batches exec steps.
MAX_EXEC_BATCH = 64


class ProtocolError(RuntimeError):
    """A verb could not be served: bad arguments, unknown run, invalid output."""


# ---------------------------------------------------------------------------
# run resolution
# ---------------------------------------------------------------------------
def resolve_run(ref: str) -> str:
    """Return the path to ``ref``'s run document: a run_id, slug, or path.

    A path is already an answer. Otherwise the state directory is searched by
    run id, then by any run whose ``slug`` / ``ticket_id`` / ``change_id``
    matches — so a driver can name a run the way a person would.
    """
    from orchestrator_next import state_dir as sd

    if not ref or not str(ref).strip():
        raise ProtocolError("missing <run>: pass a slug, run_id, or state path")
    ref = str(ref).strip()

    if os.path.sep in ref or ref.endswith((".yaml", ".yml")):
        if Path(ref).is_file():
            return ref
        raise ProtocolError(f"no state file at {ref}")

    try:
        for candidate in (ref, ref.lower()):
            path = sd.run_path(candidate)
            if path.is_file():
                return str(path)

        for run_id in sd.list_run_ids():
            path = sd.run_path(run_id)
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(raw, dict):
                continue
            identities = {
                str(raw.get(k) or "").lower()
                for k in ("slug", "ticket_id", "change_id")
            }
            if ref.lower() in identities - {""}:
                return str(path)
    except sd.StateDirError as exc:
        raise ProtocolError(str(exc)) from exc

    raise ProtocolError(f"no run found for {ref!r} (not a run_id, slug, or state path)")


# ---------------------------------------------------------------------------
# step payload construction
# ---------------------------------------------------------------------------
def _recipe_artifacts_root(state_raw: dict[str, Any]) -> str:
    """The run's recipe-declared ``artifacts_root`` template, or "" when absent.

    ``start`` copies the template into state at seed time, so the answer does
    not depend on the pack still being resolvable from wherever this process
    happens to be running. Re-reading the recipe is the fallback for runs
    seeded before that, and is given the run's own ``repo_root`` so it finds
    the pack the run was started from rather than whatever the cwd implies.
    """
    from orchestrator_next.parser import load_recipe

    persisted = state_raw.get("artifacts_root")
    if isinstance(persisted, str) and persisted:
        return persisted

    schema = str(state_raw.get("schema") or "")
    if not schema:
        return ""
    try:
        return load_recipe(schema, str(state_raw.get("repo_root") or "")).artifacts_root
    except Exception:  # noqa: BLE001 — an unreadable recipe falls back to the default
        return ""


def _artifact_base(state_raw: dict[str, Any]) -> Path:
    """Directory that named artifacts resolve against (plan Phase 2.1).

    Engine-owned by default: ``<worktree|repo>/.orchestrator/runs/<slug>/artifacts/``.
    A recipe may override the location with ``artifacts_root:`` (e.g. the pack's
    historical ``spec/changes/{slug}``). Nothing outside this function should
    assume either layout.
    """
    from orchestrator_next.paths import artifacts_dir

    return artifacts_dir(state_raw, _recipe_artifacts_root(state_raw))


def _resolve_io(specs: dict[str, dict], base: Path) -> tuple[dict[str, str], dict[str, dict]]:
    """Split an ``in:``/``out:`` block into resolved paths and a value schema.

    Artifact entries become absolute paths under ``base``; ``type:`` entries
    stay as schema so the harness knows what scalars to return.
    """
    paths: dict[str, str] = {}
    schema: dict[str, dict] = {}
    for name, spec in specs.items():
        artifact = spec.get("artifact")
        if artifact:
            paths[name] = str(base / str(artifact))
        else:
            schema[name] = {k: v for k, v in spec.items() if k != "artifact"}
    return paths, schema


def _judgment_payload(
    action: dict[str, Any],
    contract: AgentStepContract,
    state_raw: dict[str, Any],
    state_yaml_path: str,
    repo_root: str,
) -> dict[str, Any]:
    """Build the judgment payload: paths and inputs, never composed prose.

    The engine says *what* to run and *where* things live — the charter's
    path, the resolved ``in:`` paths, the ``out:`` paths and schema. Reading
    the charter and composing a prompt from it is the driver's job.
    """
    base = _artifact_base(state_raw)
    in_paths, _in_schema = _resolve_io(contract.inputs, base)
    out_paths, out_schema = _resolve_io(contract.outputs, base)
    # The step is about to write here; a judgment step should never have to
    # mkdir its own artifact base.
    if out_paths:
        base.mkdir(parents=True, exist_ok=True)

    work_dir = state_raw.get("worktree_path") or repo_root
    if not Path(work_dir).is_dir():
        work_dir = repo_root

    return {
        "status": "ready",
        "kind": KIND_JUDGMENT,
        "step_id": action["step_id"],
        "payload": {
            "step_id": action["step_id"],
            "phase": action.get("phase", "main"),
            "attempt": action.get("attempt", 1),
            # Opaque contract data, passed through untouched: the driver
            # decides what a tool list or a turn cap means.
            "max_turns": contract.max_turns,
            "tools": list(contract.tools),
            "side_effects": list(contract.side_effects),
            # The charter to read, and the dir holding anything colocated
            # with it (scenarios, learnings). The driver opens them.
            "prompt_path": contract.prompt_path,
            "prompt_dir": contract.prompt_dir or "",
            "in": in_paths,
            "out": out_paths,
            "out_schema": out_schema,
            "step_context": action.get("step_context") or {},
            "user_direction": action.get("user_direction") or "",
            "cwd": str(work_dir),
            "env": dict(action.get("env") or {}),
        },
    }


def _exec_payload(
    action: dict[str, Any],
    contract: ScriptStepContract,
    state_raw: dict[str, Any],
    state_yaml_path: str,
    state: Any,
    repo_root: str,
) -> dict[str, Any]:
    """Build the exec payload: the script to run and the env to run it in.

    The engine does not spawn anything. It hands back the absolute script
    path plus the environment block it would have applied, as *data*; the
    driver runs ``bash <run_path>`` and reports the exit code and stdout
    back through ``done``.
    """
    from orchestrator_next.step_env import inline_script_env

    base = _artifact_base(state_raw)
    in_paths, _in_schema = _resolve_io(contract.inputs, base)
    out_paths, out_schema = _resolve_io(contract.outputs, base)
    if out_paths:
        base.mkdir(parents=True, exist_ok=True)

    env = inline_script_env(
        state, state_yaml_path, action_env=dict(action.get("env") or {})
    )
    # parser absolutizes run: against the contract dir, so the script's own
    # directory IS the step dir.
    step_dir = os.path.dirname(contract.run)
    env["ORCHESTRATOR_STEP_DIR"] = step_dir
    for key, value in _contract_params(action["step_id"]).items():
        env.setdefault(key, value)

    work_dir = env.get("REPO_ROOT") or repo_root
    if not Path(work_dir).is_dir():
        work_dir = repo_root

    return {
        "status": "ready",
        "kind": KIND_EXEC,
        "step_id": action["step_id"],
        "payload": {
            "step_id": action["step_id"],
            "phase": action.get("phase", "main"),
            "attempt": action.get("attempt", 1),
            "run_path": contract.run,
            "step_dir": step_dir,
            "state_mutating": bool(contract.state_mutating),
            "tools": list(contract.tools),
            "side_effects": list(contract.side_effects),
            "in": in_paths,
            "out": out_paths,
            "out_schema": out_schema,
            "step_context": action.get("step_context") or {},
            "cwd": str(work_dir),
            # The full environment the script expects, as data. The driver
            # applies it; the engine sets nothing in its own process.
            "env": env,
        },
    }


def _contract_params(step_id: str) -> dict[str, str]:
    """A step contract's ``params:`` block, as environment strings."""
    from orchestrator_next.paths import config_root

    try:
        path = config_root() / "steps" / step_id / "contract.yaml"
        if not path.is_file():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, Exception):  # noqa: BLE001
        return {}
    params = raw.get("params") if isinstance(raw, dict) else None
    if not isinstance(params, dict):
        return {}
    return {str(k): str(v) for k, v in params.items()}


def _gate_payload(
    step_id: str,
    show: list[str],
    approve_as: str,
    state_raw: dict[str, Any],
    state_yaml_path: str,
) -> dict[str, Any]:
    """Park the run at a gate: mint (or re-return) its token and preview.

    Idempotent by construction — ``gates.issue_token`` reuses the gate's open
    record, so polling ``step`` at a blocked gate never invalidates the token a
    reviewer already has.
    """
    from orchestrator_next import gates

    entries = gates.preview(show, _show_paths(state_raw, show))
    prov = gates.provenance(state_raw, show)
    for name, entry in entries.items():
        entry.update(prov.get(name) or {})

    # A gate exists to put a human behind the work, not to rubber-stamp
    # whatever happens to be on disk. A file whose producer abandoned, or whose
    # last review rejected it, must not be presented as approvable — the
    # reviewer would be approving a token over work nothing stands behind.
    problems = gates.untrusted(prov, _fail_verdicts(state_raw, show))
    if problems:
        # A token minted on an earlier poll, before the work was rejected, is
        # now a standing licence to approve rejected work. Withdraw it: the
        # gate re-mints a fresh one once the problems are fixed.
        gates.cancel_pending(state_raw, step_id)
        state_raw["status"] = "needs_you"
        state_raw["needs_you_reason"] = (
            f"gate {step_id} cannot mint: " + "; ".join(problems)
        )
        _save_state(state_yaml_path, state_raw)
        return {
            "status": "needs_you",
            "kind": KIND_GATE,
            "step_id": step_id,
            "payload": {
                "preview": {
                    "step_id": step_id,
                    "show": entries,
                    "token_name": approve_as,
                },
            },
            "detail": f"gate {step_id} cannot mint: " + "; ".join(problems),
        }

    record = gates.issue_token(state_raw, step_id, approve_as)
    state_raw["status"] = "blocked"
    _save_state(state_yaml_path, state_raw)

    return {
        "status": "blocked",
        "kind": KIND_GATE,
        "step_id": step_id,
        "payload": {
            "preview": {
                "step_id": step_id,
                "show": entries,
                "token_name": approve_as,
            },
            "token": record["token"],
        },
    }


def _fail_verdicts(state_raw: dict[str, Any], show: list[str]) -> frozenset[str]:
    """Every enum value any contract producing a `show:` artifact calls a failure.

    Read from the same `fail_on:` the routing uses, so a gate and the router
    never disagree about what "rejected" means.
    """
    from orchestrator_next.parser import phase_nodes

    values: set[str] = set()
    state = load_state_from_raw(state_raw)
    for phase in state.workflow_plan:
        for node in phase_nodes(state, phase):
            step_id = str(node.get("id", ""))
            if not step_id:
                continue
            try:
                contract = load_contract_for_step(step_id)
            except Exception:  # noqa: BLE001 — a gate check is best-effort
                continue
            declared = getattr(contract, "outputs", None) or {}
            if not any(n in show for n in declared):
                continue
            for spec in declared.values():
                fail_on = spec.get("fail_on")
                if isinstance(fail_on, list):
                    values.update(str(v) for v in fail_on)
    return frozenset(values)


def _show_paths(state_raw: dict[str, Any], show: list[str]) -> dict[str, str]:
    """Resolve the gate's ``show:`` artifact names to paths.

    A name is whatever an upstream step declared under ``out:``; the last
    producer wins, which is what a reviewer wants to see. Names the recipe
    never produced resolve to nothing and are reported as missing.
    """
    from orchestrator_next.parser import phase_nodes

    base = _artifact_base(state_raw)
    wanted = set(show)
    found: dict[str, str] = {}
    state = load_state_from_raw(state_raw)
    for phase in state.workflow_plan:
        for node in phase_nodes(state, phase):
            step_id = str(node.get("id", ""))
            if not step_id:
                continue
            try:
                contract = load_contract_for_step(step_id)
            except Exception:  # noqa: BLE001 — a gate preview is best-effort
                continue
            for name, spec in (getattr(contract, "outputs", None) or {}).items():
                if name in wanted and spec.get("artifact"):
                    found[name] = str(base / str(spec["artifact"]))
    return found


def load_state_from_raw(state_raw: dict[str, Any]) -> Any:
    """A State view over an in-memory doc, for helpers that walk the plan."""
    from orchestrator_next.parser import State

    return State(
        change_id=str(state_raw.get("change_id") or ""),
        phase=str(state_raw.get("phase") or "main"),
        repo_root=str(state_raw.get("repo_root") or ""),
        workflow_dir=str(state_raw.get("worktree_path") or ""),
        workflow_plan=state_raw.get("workflow_plan") or {},
        step_history=[],
        raw=state_raw,
    )


def _save_state(state_yaml_path: str, state_raw: dict[str, Any]) -> None:
    """Write the run doc back through the state store, then the RunStore."""
    from orchestrator_next import state_store

    handle = state_store.parse_handle(state_yaml_path)
    store, h = state_store.open_store(handle)
    try:
        _doc, token = store.load(h)
    except (state_store.StateNotFoundError, OSError):
        return
    try:
        store.save(h, state_raw, token)
    except (state_store.StateConflictError, yaml.YAMLError):
        return


# ---------------------------------------------------------------------------
# verb: step
# ---------------------------------------------------------------------------
def step(run_ref: str, *, user_direction: str = "") -> tuple[dict[str, Any], int]:
    """Report the one step that is ready now — judgment, exec, or gate.

    Returns ``(result, exit_code)``. ``result['status']`` is one of
    ``ready|done|blocked|needs_you|error``. The engine runs nothing: an exec
    step comes back as a payload naming the script and its environment, and
    the driver reports the outcome through ``done``.

    ``user_direction`` is free-form text from `resume` that matched no
    await_input option. It rides along on the payload of the step that asked,
    for the driver to pass on; it is not carried past that step.
    """
    from orchestrator_next.dispatch import (
        EXIT_GATE_REQUIRED,
        EXIT_NEEDS_YOU,
        ContractDispatchError,
        dispatch,
    )
    from orchestrator_next.execute import _finalize_state

    state_yaml_path = resolve_run(run_ref)
    repo_root = os.environ.get("REPO_ROOT", "") or os.getcwd()

    if not Path(state_yaml_path).is_file():
        return {"status": "done", "step_id": None,
                "detail": "run archived"}, 0  # archived: nothing left to report
    state = load_state(state_yaml_path)
    _pin_config(state.raw)
    if isinstance(state.raw.get("awaiting"), dict):
        # Parked on a question from an earlier turn. `orchestrator resume`
        # is what clears it; dispatching would re-run the parked step.
        return _awaiting_result(state_yaml_path), 0
    try:
        action, code = dispatch(state, state_yaml_path)
    except (ContractDispatchError, ContractNotFoundError, ContractError) as exc:
        return {"status": "error", "step_id": None, "detail": str(exc)}, 0
    except FileNotFoundError as exc:
        return {"status": "error", "step_id": None, "detail": str(exc)}, 0

    if code == 1:
        # A finished run flips to `completed`, clears next_step, and drops
        # its scratch dir. Under the old self-drive loop this happened in
        # the loop's exit arm; `step` is the only thing that sees the run
        # finish now.
        _finalize_state(state_yaml_path)
        return _terminal_report(
            {"status": "done", "step_id": None}, state_yaml_path
        ), 0
    if code == 2:
        return {
            "status": "blocked",
            "kind": None,
            "step_id": (action or {}).get("step_id"),
            "detail": (action or {}).get("reason") or "blocked (signoff or halt)",
        }, 0
    if code == EXIT_NEEDS_YOU:
        # A node abandoned and nothing downstream can ever run. No `ask`:
        # the engine has no question, it has a dead end the human must
        # resolve (retry the step with `reset-step`, edit the recipe, or
        # abort). `payload.abandoned_step` + `payload.reason` let a
        # harness (the Claude Mod, the fallback skill) offer a retry
        # without re-parsing `detail`.
        abandoned_step = (action or {}).get("step_id")
        reason = (action or {}).get("detail") or "step abandoned"
        return {
            "status": "needs_you",
            "kind": KIND_JUDGMENT,
            "step_id": abandoned_step,
            "detail": reason,
            "payload": {
                "reason": reason,
                "abandoned_step": abandoned_step,
            },
        }, 0
    if code == EXIT_GATE_REQUIRED:
        # A human has to approve the gate before this step may run; the
        # engine has nothing further to decide (docs/protocol-v2.md §7).
        return {
            "status": "needs_you",
            "kind": KIND_GATE,
            "step_id": (action or {}).get("step_id"),
            "requires": (action or {}).get("requires"),
            "detail": (action or {}).get("detail") or "gate token required",
        }, 0
    if code != 0:
        return {"status": "error", "step_id": None,
                "detail": f"dispatch exit {code}"}, 0

    step_id = action["step_id"]

    # A gate lives in the recipe, not in steps/: dispatch tags the action
    # rather than loading a contract that does not exist.
    if action.get("kind") == KIND_GATE:
        return _gate_payload(
            step_id,
            list(action.get("show") or []),
            str(action.get("approve_as") or ""),
            state.raw,
            state_yaml_path,
        ), 0

    try:
        contract = load_contract_for_step(step_id)
    except (FileNotFoundError, ContractError, ContractNotFoundError) as exc:
        return {"status": "error", "step_id": step_id, "detail": str(exc)}, 0

    if isinstance(contract, GateStepContract):
        # A pack that still ships a `kind: gate` contract file: the
        # contract carries show/approve_as instead of the recipe entry.
        return _gate_payload(
            step_id, list(contract.show), contract.approve_as,
            state.raw, state_yaml_path,
        ), 0

    if isinstance(contract, AgentStepContract):
        if user_direction:
            # Reported as its own field: the engine composes no prose, so
            # the driver decides how to put this in front of the agent.
            action["user_direction"] = user_direction
        result = _judgment_payload(
            action, contract, state.raw, state_yaml_path, repo_root
        )
        return result, 0

    # exec step: hand the script to the driver, same as a judgment step.
    if user_direction:
        action.setdefault("env", {})
        action["env"]["ORCHESTRATOR_USER_DIRECTION"] = user_direction
    result = _exec_payload(
        action, contract, state.raw, state_yaml_path, state, repo_root
    )
    return result, 0


def _awaiting_result(state_yaml_path: str) -> dict[str, Any]:
    """The `needs_you` result for a run parked on an await_input step."""
    raw = yaml.safe_load(Path(state_yaml_path).read_text(encoding="utf-8")) or {}
    awaiting = raw.get("awaiting") or {}
    return {
        "status": "needs_you",
        "kind": KIND_JUDGMENT,
        "step_id": awaiting.get("step_id"),
        "payload": {
            "ask": awaiting.get("ask") or "",
            "options": awaiting.get("options") or [],
        },
        "detail": awaiting.get("ask") or "awaiting input",
    }


# ---------------------------------------------------------------------------
# verb: resume
# ---------------------------------------------------------------------------
def resume(run_ref: str, text: str) -> tuple[dict[str, Any], int]:
    """Answer a run parked on await_input, then hand back the next step.

    A matched option is applied by the engine — advance, or reset the DAG to
    the option's ``reset_to`` — without re-running the parked step. Text that
    matches nothing is passed to the step itself on its next dispatch, which
    is how a free-form answer reaches the agent that asked.
    """
    from orchestrator_next.execute import route_awaiting_input

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)
    if not isinstance(state.raw.get("awaiting"), dict):
        raise ProtocolError(f"run {run_ref} is not awaiting input")

    matched = route_awaiting_input(state_yaml_path, text)
    if not matched:
        # No option matched. Clear the block so the parked step is dispatched
        # again, and hand it the raw text to interpret itself.
        raw = yaml.safe_load(Path(state_yaml_path).read_text(encoding="utf-8")) or {}
        raw.pop("awaiting", None)
        _save_state(state_yaml_path, raw)

    next_result, _ = step(state_yaml_path, user_direction="" if matched else text)
    return {"status": "ok", "matched": matched, "next": next_result}, 0


def _pin_config(state_raw: dict[str, Any]) -> None:
    """Point ORCHESTRATOR_CONFIG at the pack root this run was seeded from.

    ``start`` resolves the root once (from ``--config`` or the environment)
    and writes it into the run doc, so ``step`` / ``done`` / ``status`` need
    neither the flag nor the variable: the run carries its own pack.
    """
    if os.environ.get("ORCHESTRATOR_CONFIG"):
        return
    root = str(state_raw.get("config_root") or "")
    if root:
        os.environ["ORCHESTRATOR_CONFIG"] = root
    repo = str(state_raw.get("repo_root") or "")
    if repo and not os.environ.get("REPO_ROOT"):
        os.environ["REPO_ROOT"] = repo


# ---------------------------------------------------------------------------
# verb: start
# ---------------------------------------------------------------------------
def start(
    recipe_ref: str,
    slug: str,
    *,
    inputs: dict[str, Any] | None = None,
    ticket_id: str = "",
    config: str = "",
) -> tuple[dict[str, Any], int]:
    """Seed a run and return its identity plus the first ``step`` result.

    ``config`` is the pack root (``--config``), and wins over
    ORCHESTRATOR_CONFIG. The resolved root is persisted on the run, so no
    later verb needs either.
    """
    from orchestrator_next.paths import ConfigRootError, WorkflowRefError, resolve_workflow_ref
    from orchestrator_next.paths import new_run_id as paths_new_run_id
    from orchestrator_next import state_dir as sd
    from orchestrator_next.seed import seed_state_file

    if not slug or not slug.strip():
        raise ProtocolError("missing <slug>")
    slug = slug.strip()

    # Resume rather than re-seed: a second `start` on a live slug used to mint a
    # fresh run_id and strand the first one mid-step, so a driver that calls
    # `start` to pick a run back up (the Claude mod's `run` tool does) lost the
    # work already recorded. An unresolvable slug falls through and seeds.
    try:
        existing = resolve_run(slug)
    except ProtocolError:
        existing = ""
    if existing:
        raw = yaml.safe_load(Path(existing).read_text(encoding="utf-8")) or {}
        next_result, _code = step(existing)
        return {
            "run_id": str(raw.get("run_id") or ""),
            "slug": slug.lower(),
            "state": existing,
            "resumed": True,
            "next": next_result,
        }, 0

    repo_root = os.environ.get("REPO_ROOT", "") or os.getcwd()
    if config:
        root = Path(config)
        if not (root / "workflows").is_dir():
            raise ProtocolError(
                f"--config {config!r} is not a pack root (no workflows/ in it)"
            )
        os.environ["ORCHESTRATOR_CONFIG"] = str(root.resolve())
    try:
        config_pack, schema, cfg_root = resolve_workflow_ref(
            recipe_ref, Path(repo_root)
        )
    except (WorkflowRefError, ConfigRootError, FileNotFoundError) as exc:
        raise ProtocolError(str(exc)) from exc
    os.environ["ORCHESTRATOR_CONFIG"] = str(cfg_root)

    run_id = paths_new_run_id()
    try:
        state_path = sd.run_path(run_id)
    except sd.StateDirError as exc:
        raise ProtocolError(str(exc)) from exc
    state_path.parent.mkdir(parents=True, exist_ok=True)
    user_input = json.dumps(inputs, sort_keys=True) if inputs else slug
    try:
        seed_state_file(
            state_path,
            slug=slug.lower(),
            schema=schema,
            repo_root=repo_root,
            config_pack=config_pack,
            config_root=str(cfg_root),
            user_input=user_input,
            ticket_id=ticket_id,
            run_id=run_id,
        )
    except (FileNotFoundError, ValueError) as exc:
        state_path.unlink(missing_ok=True)
        raise ProtocolError(str(exc)) from exc

    if inputs:
        raw = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
        raw["inputs"] = inputs
        state_path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    next_result, _code = step(str(state_path))
    return {
        "run_id": run_id,
        "slug": slug.lower(),
        "state": str(state_path),
        "next": next_result,
    }, 0


# ---------------------------------------------------------------------------
# verb: done
# ---------------------------------------------------------------------------
def validate_out(
    contract: Any, out: dict[str, Any], state_raw: dict[str, Any]
) -> list[str]:
    """Return the list of ``out`` violations for a structured done payload.

    Artifact outs must exist on disk under the run's artifact base; ``type:
    enum`` outs must carry one of the declared values; any other declared
    ``type:`` out must simply be present. An empty list means the payload
    satisfies the contract.
    """
    declared = getattr(contract, "outputs", None) or {}
    if not declared:
        return []
    base = _artifact_base(state_raw)
    problems: list[str] = []
    for name, spec in declared.items():
        optional = bool(spec.get("optional"))
        artifact = spec.get("artifact")
        if artifact:
            path = Path(str(out.get(name) or (base / str(artifact))))
            if not path.is_absolute():
                path = base / path
            if not path.is_file():
                if not optional:
                    problems.append(f"out.{name}: artifact not found at {path}")
            continue
        if name not in out or out[name] is None:
            if not optional:
                problems.append(f"out.{name}: missing (declared type: {spec.get('type')})")
            continue
        if spec.get("type") == "enum":
            values = spec.get("values") or []
            if out[name] not in values:
                problems.append(
                    f"out.{name}: {out[name]!r} not one of {values}"
                )
    return problems


#: `next.status` values that mean the run will not advance again.
TERMINAL_STATUSES = frozenset({"done", "error"})


def _run_report(state_yaml_path: str) -> dict[str, Any] | None:
    """The run as ``status`` reports it, or None when it cannot be built."""
    try:
        # `status` is a parameter name in `done`, so reach the verb through
        # the module rather than the shadowed local.
        import sys as _sys

        report, _ = _sys.modules[__name__].status(state_yaml_path)
    except (ProtocolError, OSError):
        return None
    return report


def _terminal_report(result: dict[str, Any], state_yaml_path: str) -> dict[str, Any]:
    """Attach the full run report to a terminal `step` answer.

    Same builder as `done` uses, so a driver that crashed and re-asked `step`
    gets exactly the report the finishing `done` would have carried.
    """
    if str(result.get("status") or "") not in TERMINAL_STATUSES:
        return result
    report = _run_report(state_yaml_path)
    if report is not None:
        result["report"] = report
    return result


def _with_report(result: dict[str, Any], state_yaml_path: str) -> dict[str, Any]:
    """Attach the full run report once the run has reached a terminal state.

    A driver wants the whole picture exactly when the run stops — not after
    every step. The report is whatever ``status`` builds, so there is one
    projection of a run and no second one to drift.
    """
    nxt = result.get("next") or {}
    if str(nxt.get("status") or "") not in TERMINAL_STATUSES:
        return result
    # `step` already built the report on its terminal answer; lift it rather
    # than building a second one, and drop the nested copy so the payload
    # carries the run exactly once.
    report = nxt.pop("report", None)
    if report is None:
        report = _run_report(state_yaml_path)
    if report is not None:
        result["report"] = report
    return result


def _exec_done_payload(
    step_id: str,
    state: Any,
    contract: Any,
    exit_code: int,
    stdout_file: str,
    out: dict[str, Any],
) -> dict[str, Any]:
    """Turn a driver-reported script outcome into a record payload."""
    phase = state.phase or "main"
    attempt = compute_attempt(
        state.step_history, phase, step_id, include_in_progress=True
    )

    if exit_code != 0:
        return {
            "step_id": step_id, "phase": phase, "attempt": attempt,
            "status": "failed",
            "outputs": {"reason": f"script exited {exit_code}"},
            "evidence": {"summary": f"script exited {exit_code}"},
        }

    stdout = ""
    if stdout_file:
        try:
            stdout = Path(stdout_file).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ProtocolError(f"--stdout-file unreadable: {exc}") from exc

    status, outputs, patch = script_result(stdout)
    # An explicit --out overlays whatever the script printed.
    outputs.update(out)
    if status != "await_input" and not str(outputs.get("reason") or "").strip():
        outputs["reason"] = "script completed"

    payload: dict[str, Any] = {
        "step_id": step_id, "phase": phase, "attempt": attempt,
        "status": status, "outputs": outputs,
        "evidence": {"outputs": outputs, "summary": f"script {status}"},
    }
    if patch is not None:
        payload["state_patch"] = patch
    return payload


def script_result(stdout: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Parse a script step's stdout into ``(status, outputs, state_patch)``.

    The protocol is the last JSON line of stdout, in either shape:

      ``{"status": "...", "outputs": {...}}``   — explicit status
      ``{"status": "...", "k": v, ...}``        — status plus flat outputs
      ``{"k": v, ...}``                         — flat outputs, status completed

    ``state_patch`` is lifted from either the top level or ``outputs``.
    Unparseable or absent stdout means a plain completed step with no outputs
    — a script that prints nothing is not an error.
    """
    parsed: Any = {}
    lines = (stdout or "").strip().splitlines()
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except (json.JSONDecodeError, ValueError):
            parsed = {}
    if not isinstance(parsed, dict):
        return "completed", {}, None

    raw_status = parsed.get("status")
    raw_outputs = parsed.get("outputs")
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
    return status, outputs, patch if isinstance(patch, dict) else None


def done(
    run_ref: str,
    step_id: str,
    *,
    out: dict[str, Any] | None = None,
    status: str = "completed",
    exit_code: int | None = None,
    stdout_file: str = "",
) -> tuple[dict[str, Any], int]:
    """Record a step's result, then return the next step.

    A judgment step reports ``--out`` (rejected, exit 3, when it does not
    satisfy the contract's ``out:`` block). An exec step reports what the
    driver observed running the script: ``--exit-code`` and, optionally,
    ``--stdout-file``. The engine parses that stdout for the script protocol
    (``status`` / outputs / ``state_patch``) and routes exactly as it did
    when it ran the script itself: a non-zero exit records ``failed`` and
    takes failure routing, ``await_input`` parks the run.
    """
    from orchestrator_next.execute import _record_with_retry

    out = dict(out or {})
    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    try:
        contract = load_contract_for_step(step_id)
    except (FileNotFoundError, ContractError, ContractNotFoundError) as exc:
        raise ProtocolError(str(exc)) from exc

    is_exec = isinstance(contract, ScriptStepContract)
    if exit_code is not None and not is_exec:
        raise ProtocolError(
            f"--exit-code is for exec steps; {step_id} is {contract.kind}"
        )
    if is_exec and exit_code is None:
        raise ProtocolError(
            f"exec step {step_id} needs --exit-code (and --stdout-file when the "
            "script printed a result)"
        )

    if is_exec:
        payload = _exec_done_payload(
            step_id, state, contract, exit_code or 0, stdout_file, out
        )
    else:
        if status not in {"completed", "abandoned"}:
            raise ProtocolError(
                f"--status must be completed or abandoned (got {status!r})"
            )
        if status == "completed":
            problems = validate_out(contract, out, state.raw)
            if problems:
                raise ProtocolError(
                    "out does not satisfy the step contract: " + "; ".join(problems)
                )
        outputs = dict(out)
        outputs.setdefault("reason", f"{step_id} {status} (structured out)")
        payload = {
            "step_id": step_id,
            "phase": state.phase or "main",
            "status": status,
            "outputs": outputs,
        }

    result, code = _record_with_retry(state_yaml_path, payload)
    if code != 0:
        raise ProtocolError(
            f"record rejected the done payload: {json.dumps(result, sort_keys=True)}"
        )

    next_result, _ = step(state_yaml_path)
    return _with_report({
        "status": "ok",
        "step_id": step_id,
        "attempt": (result or {}).get("attempt"),
        "next": next_result,
    }, state_yaml_path), 0


# ---------------------------------------------------------------------------
# verbs: approve / cancel
# ---------------------------------------------------------------------------
def approve(
    run_ref: str, token: str, *, edits: dict[str, Any] | None = None
) -> tuple[dict[str, Any], int]:
    """Approve a blocked gate and hand back the step that follows it.

    Completing the gate node is what unblocks its dependents; binding the token
    to its ``approve_as`` name is what satisfies any downstream
    ``requires:``. ``edits`` is stored verbatim in the gate record — the engine
    never interprets it, it is the reviewer's note to the next step.
    """
    from orchestrator_next import gates, readiness

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    try:
        record = gates.approve_token(state.raw, token, edits)
    except gates.GateError as exc:
        raise ProtocolError(str(exc)) from exc

    gate_id = str(record.get("gate_id") or "")
    readiness.mark_node_status(state.raw, state.phase, gate_id, "completed")
    state.raw["status"] = "active"
    _append_gate_history(state.raw, record)
    _save_state(state_yaml_path, state.raw)

    next_result, _ = step(state_yaml_path)
    return {
        "status": "ok",
        "gate_id": gate_id,
        "token_name": record.get("token_name"),
        "approved_at": record.get("approved_at"),
        "edits": record.get("edits"),
        "next": next_result,
    }, 0


def _append_gate_history(state_raw: dict[str, Any], record: dict[str, Any]) -> None:
    """Record the approval in step_history so `events` and the report see it."""
    history = state_raw.setdefault("step_history", [])
    if not isinstance(history, list):
        return
    entry = {
        "step_id": record.get("gate_id"),
        "phase": state_raw.get("phase") or "main",
        "status": "completed",
        "attempt": 1,
        "started_at": record.get("issued_at"),
        "ended_at": record.get("approved_at"),
        "kind": KIND_GATE,
        "outputs": {
            "reason": f"gate {record.get('gate_id')} approved",
            "token_name": record.get("token_name"),
        },
    }
    if record.get("edits") is not None:
        entry["outputs"]["edits"] = record["edits"]
    history.append(entry)


def reset_step(run_ref: str, step_id: str) -> tuple[dict[str, Any], int]:
    """Reset ``step_id`` (and everything declared after it) back to pending.

    Used to retry a run parked at ``needs_you`` because a judgment step was
    recorded ``abandoned`` (e.g. the harness's spawn was refused) — there is
    no automatic routing for that dead end, so a human decides to retry.
    Clears the run's ``needs_you`` status back to ``active`` and returns the
    next ``step`` result, exactly like `approve`/`resume`/`done` do.
    """
    from orchestrator_next.reset_step import reset_step as _reset_step_file

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    try:
        reset_ids = _reset_step_file(step_id, state_yaml_path)
    except (ValueError, FileNotFoundError) as exc:
        raise ProtocolError(str(exc)) from exc


    next_result, _ = step(state_yaml_path)
    return {
        "status": "ok",
        "step_id": step_id,
        "reset": reset_ids,
        "next": next_result,
    }, 0


def cancel(run_ref: str) -> tuple[dict[str, Any], int]:
    """Abort a run: every pending gate is cancelled and the run is closed."""
    from orchestrator_next import gates

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    cancelled = []
    for record in gates.gate_records(state.raw):
        if record.get("status") == "pending":
            record["status"] = "cancelled"
            cancelled.append(record.get("gate_id"))
    state.raw["status"] = "cancelled"
    _save_state(state_yaml_path, state.raw)

    result, _ = status(state_yaml_path)
    result["cancelled_gates"] = cancelled
    return result, 0


# ---------------------------------------------------------------------------
# verbs: status / events
# ---------------------------------------------------------------------------
def status(run_ref: str) -> tuple[dict[str, Any], int]:
    """Report nodes, artifacts, and gate state for a run.

    State only: what each node is, what it produced, and where the run
    stands. Metrics, cost and usage belong to the driver that ran the steps.
    """
    from orchestrator_next import gates
    from orchestrator_next.parser import phase_nodes

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    attempts: dict[str, int] = {}
    for entry in state.step_history:
        if entry.attempt:
            attempts[entry.step_id] = max(attempts.get(entry.step_id, 0), int(entry.attempt))

    nodes = []
    all_artifacts: list[dict[str, Any]] = []
    for phase in state.workflow_plan:
        for node in phase_nodes(state, phase):
            step_id = str(node.get("id", ""))
            if not step_id:
                continue
            node_artifacts = [
                a for a in (node.get("artifacts") or []) if isinstance(a, dict)
            ]
            nodes.append({
                "id": step_id,
                "phase": phase,
                "kind": (KIND_GATE if gates.node_is_gate(node)
                         else _kind_of(step_id)),
                "status": str(node.get("status") or "pending"),
                "attempts": attempts.get(step_id, 0),
                "artifacts": node_artifacts,
            })
            for a in node_artifacts:
                all_artifacts.append({**a, "step_id": step_id})

    return {
        "run_id": str(state.raw.get("run_id") or Path(state_yaml_path).stem),
        "slug": state.raw.get("slug") or state.change_id,
        "state": state_yaml_path,
        "run_status": state.raw.get("status") or "active",
        "phase": state.phase,
        "nodes": nodes,
        "artifacts": all_artifacts,   # {name, path, sha256, step_id}
        "artifacts_base": str(_artifact_base(state.raw)),
        # The pack this run was seeded from, so a driver reading only this
        # report knows which charters the step ids refer to.
        "config_root": str(state.raw.get("config_root") or ""),
        # Every attempt, in order — what `events` used to project.
        "step_history": [dict(entry.raw) for entry in state.step_history],
        # The most recently approved token, and every gate this run has seen.
        "gate_token": gates.latest_approved_token(state.raw),
        "gates": gates.gate_records(state.raw),
    }, 0


def _kind_of(step_id: str) -> str:
    try:
        contract = load_contract_for_step(step_id)
    except Exception:  # noqa: BLE001 — a missing contract is not a status failure
        return "unknown"
    return getattr(contract, "kind", KIND_JUDGMENT)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def _pop_flag(args: list[str], flag: str) -> str | None:
    """Remove ``--flag value`` from args and return the value."""
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise ProtocolError(f"{flag} needs a value")
    value = args[i + 1]
    del args[i:i + 2]
    return value


def _json_flag(args: list[str], flag: str, *, default: Any = None) -> Any:
    raw = _pop_flag(args, flag)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{flag} must be valid JSON — {exc}") from exc


def main(verb: str, argv: list[str]) -> int:
    """Dispatch one verb. JSON is the only output; exit 3 on an engine error.

    ``--state <dir>`` names where run documents live and is accepted by every
    verb (ORCHESTRATOR_STATE is the fallback). A protocol status — blocked,
    needs_you, even a failed run — is a successful call and exits 0; only a
    usage or infrastructure error exits non-zero.
    """
    args = list(argv)
    # Accepted and ignored: JSON is the only format there is now.
    while "--json" in args:
        args.remove("--json")
    try:
        state_flag = _pop_flag(args, "--state")
        if state_flag:
            from orchestrator_next.state_dir import ENV_STATE_DIR
            os.environ[ENV_STATE_DIR] = state_flag
        if verb == "start":
            if len(args) < 2:
                raise ProtocolError("usage: orchestrator start <recipe> <slug> "
                                    "--state DIR [--config PATH] "
                                    "[--inputs JSON] [--ticket-id ID]")
            inputs = _json_flag(args, "--inputs")
            ticket_id = _pop_flag(args, "--ticket-id") or ""
            config = _pop_flag(args, "--config") or ""
            if inputs is not None and not isinstance(inputs, dict):
                raise ProtocolError("--inputs must be a JSON object")
            result, code = start(
                args[0], args[1], inputs=inputs, ticket_id=ticket_id, config=config,
            )
        elif verb == "step":
            if not args:
                raise ProtocolError("usage: orchestrator step <run> --state DIR")
            result, code = step(args[0])
        elif verb == "done":
            if len(args) < 2:
                raise ProtocolError(
                    "usage: orchestrator done <run> <step_id> "
                    "[--out JSON] [--status completed|abandoned]   (judgment)\n"
                    "       orchestrator done <run> <step_id> "
                    "--exit-code N [--stdout-file PATH]            (exec)"
                )
            run_ref, step_id = args[0], args[1]
            rest = args[2:]
            out = _json_flag(rest, "--out", default={})
            st = _pop_flag(rest, "--status") or "completed"
            raw_exit = _pop_flag(rest, "--exit-code")
            stdout_file = _pop_flag(rest, "--stdout-file") or ""
            if not isinstance(out, dict):
                raise ProtocolError("--out must be a JSON object")
            try:
                exit_code = None if raw_exit is None else int(raw_exit)
            except ValueError:
                raise ProtocolError(
                    f"--exit-code takes a whole number, not {raw_exit!r}"
                ) from None
            result, code = done(
                run_ref, step_id, out=out, status=st,
                exit_code=exit_code, stdout_file=stdout_file,
            )
        elif verb == "approve":
            if len(args) < 2:
                raise ProtocolError(
                    "usage: orchestrator approve <run> <token> --state DIR [--edits JSON]"
                )
            run_ref, token = args[0], args[1]
            rest = args[2:]
            edits = _json_flag(rest, "--edits")
            if edits is not None and not isinstance(edits, dict):
                raise ProtocolError("--edits must be a JSON object")
            result, code = approve(run_ref, token, edits=edits)
        elif verb == "resume":
            if len(args) < 2:
                raise ProtocolError('usage: orchestrator resume <run> "<text>" --state DIR')
            result, code = resume(args[0], " ".join(args[1:]).strip())
        elif verb == "cancel":
            if not args:
                raise ProtocolError("usage: orchestrator cancel <run> --state DIR")
            result, code = cancel(args[0])
        elif verb == "reset-step":
            if len(args) < 2:
                raise ProtocolError(
                    "usage: orchestrator reset-step <run> <step_id> --state DIR"
                )
            result, code = reset_step(args[0], args[1])
        elif verb == "status":
            if not args:
                raise ProtocolError("usage: orchestrator status <run> --state DIR")
            result, code = status(args[0])
        else:  # pragma: no cover — cli.py routes only the verbs above
            raise ProtocolError(f"unknown protocol verb: {verb}")
    except ProtocolError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return EXIT_ERROR

    print(json.dumps(result, sort_keys=True, indent=2, default=str))
    sys.stdout.flush()
    return code
