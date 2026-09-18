"""Protocol v2 CLI verbs: ``start`` / ``step`` / ``done`` / ``status`` / ``events``.

See ``docs/protocol-v2.md`` §3-§5. The engine never spawns a model here: it
computes the next step, hands the harness a payload, validates what comes
back, and records it.

Every verb in this module exits 0 on success and encodes the run's condition
in the JSON ``status`` field; exit 3 is reserved for an engine error (bad
arguments, unknown run, rejected ``done`` payload). The pre-v2 verbs
(``next`` / ``done <state.yaml>`` / ``run``) keep their own exit-code
protocol untouched.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import (
    AgentStepContract,
    GateStepContract,
    KIND_EXEC,
    KIND_GATE,
    KIND_JUDGMENT,
    ContractError,
    ContractNotFoundError,
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
    """Return a state handle for ``ref``: a slug, a run_id, or a state path.

    Tried in order — an existing path or store URL wins, then a live run in the
    RunStore (by run_id, then lowercased), then a run whose ``slug`` /
    ``ticket_id`` / ``change_id`` matches. Raises ProtocolError when nothing
    resolves, so the caller can report it rather than seeding a second run.
    """
    from orchestrator_next import state_store
    from orchestrator_next.run_store import materialize, open_store

    if not ref or not str(ref).strip():
        raise ProtocolError("missing <run>: pass a slug, run_id, or state path")
    ref = str(ref).strip()

    # A path or an explicit store URL is already a handle.
    if "://" in ref or os.path.sep in ref or ref.endswith((".yaml", ".yml")):
        handle = state_store.parse_handle(ref)
        if not handle.is_file or Path(handle.location).is_file():
            return ref
        raise ProtocolError(f"no state file at {ref}")

    store = open_store()
    repo_root = os.environ.get("REPO_ROOT", "")
    for candidate in (ref, ref.lower()):
        if store.load(candidate) is not None:
            return str(materialize(store, candidate, repo_root=repo_root))

    # Slug / ticket-id lookup: scan live runs for a matching identity field.
    for run_id in store.list_ids():
        text = store.load(run_id)
        if not text:
            continue
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(raw, dict):
            continue
        identities = {
            str(raw.get(k) or "").lower()
            for k in ("slug", "ticket_id", "change_id")
        }
        if ref.lower() in identities - {""}:
            return str(materialize(store, run_id, repo_root=repo_root))

    raise ProtocolError(f"no run found for {ref!r} (not a run_id, slug, or state path)")


def _persist(state_yaml_path: str) -> None:
    """Write a materialized run back to the RunStore (no-op otherwise)."""
    from orchestrator_next.run_store import _state_root, open_store, persist

    path = Path(state_yaml_path)
    try:
        if path.parent.resolve() != _state_root().resolve():
            return
    except OSError:
        return
    try:
        persist(open_store(), path.stem, path)
    except (OSError, ValueError):
        pass


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


def _scratch_base(state_raw: dict[str, Any]) -> Path:
    """The run's throwaway workspace — created on demand, discarded on archive."""
    from orchestrator_next.paths import scratch_dir

    return scratch_dir(state_raw)


def render_placeholders(
    text: str, in_paths: dict[str, str], out_paths: dict[str, str]
) -> str:
    """Substitute ``{in.x}`` / ``{out.y}`` in a charter with resolved paths.

    Unknown names are left verbatim so a prompt never silently loses meaning;
    ``validate-workflow`` is what turns an unknown name into an error.
    """
    if "{in." not in text and "{out." not in text:
        return text
    for prefix, mapping in (("in", in_paths), ("out", out_paths)):
        for name, path in mapping.items():
            text = text.replace("{%s.%s}" % (prefix, name), path)
    return text


def placeholder_names(text: str) -> set[tuple[str, str]]:
    """Every ``{in.x}`` / ``{out.y}`` reference in a charter, as (side, name)."""
    return {
        (m.group(1), m.group(2))
        for m in re.finditer(r"\{(in|out)\.([A-Za-z0-9_-]+)\}", text or "")
    }


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
    """Build the protocol-v2 judgment payload (docs/protocol-v2.md §4)."""
    from orchestrator_next import model_routes
    from orchestrator_next.execute import (
        _structured_output_contract,
        build_agent_payload,
        resolve_models_yaml,
    )

    models_yaml = resolve_models_yaml(repo_root=repo_root)
    base = _artifact_base(state_raw)
    in_paths, _in_schema = _resolve_io(contract.inputs, base)
    out_paths, out_schema = _resolve_io(contract.outputs, base)
    # The step is about to write here; a judgment step should never have to
    # mkdir its own artifact base.
    if out_paths:
        base.mkdir(parents=True, exist_ok=True)

    output_contract = _structured_output_contract(
        action["step_id"], out_paths, out_schema
    )
    base_payload = build_agent_payload(
        action,
        repo_root=repo_root,
        models_yaml=models_yaml,
        state_raw=state_raw,
        state_yaml_path=state_yaml_path,
        output_contract=output_contract,
    )
    route = model_routes.resolve_route(action["model"], models_yaml)

    return {
        "status": "ready",
        "kind": KIND_JUDGMENT,
        "step_id": action["step_id"],
        "payload": {
            "step_id": action["step_id"],
            "phase": action.get("phase", "main"),
            "attempt": action.get("attempt", 1),
            "model": action["model"],
            "model_id": route.get("model_id") or "",
            "max_turns": contract.max_turns,
            "tools": list(contract.tools),
            "side_effects": list(contract.side_effects),
            "system": render_placeholders(
                base_payload["prompt"], in_paths, out_paths
            ),
            "in": in_paths,
            "out": out_paths,
            "out_schema": out_schema,
            "cwd": base_payload["cwd"],
            "env": base_payload.get("env") or {},
        },
    }


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
    _persist(state_yaml_path)


# ---------------------------------------------------------------------------
# verb: step
# ---------------------------------------------------------------------------
def step(run_ref: str, *, user_direction: str = "") -> tuple[dict[str, Any], int]:
    """Advance the run: execute consecutive exec steps, stop at judgment/gate.

    Returns ``(result, exit_code)``. ``result['status']`` is one of
    ``ready|running|done|blocked|needs_you|error`` (protocol v2 §3).

    ``user_direction`` is free-form text from `resume` that matched no
    await_input option. It reaches the step that asked — an exec step through
    ``ORCHESTRATOR_USER_DIRECTION``, a judgment step appended to its prompt —
    and is consumed by the first step dispatched, not carried onward.
    """
    from orchestrator_next.dispatch import (
        EXIT_GATE_REQUIRED,
        EXIT_NEEDS_YOU,
        ContractDispatchError,
        dispatch,
    )
    from orchestrator_next.execute import _finalize_state, run_script_step

    state_yaml_path = resolve_run(run_ref)
    repo_root = os.environ.get("REPO_ROOT", "") or os.getcwd()

    for _ in range(MAX_EXEC_BATCH):
        if not Path(state_yaml_path).is_file():
            return {"status": "done", "step_id": None,
                    "detail": "run archived"}, 0
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
            _persist(state_yaml_path)
            return {"status": "done", "step_id": None}, 0
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
                base = action.get("instruction") or ""
                action["instruction"] = (
                    f"{base}\n\nUser direction: {user_direction}"
                    if base else f"User direction: {user_direction}"
                )
            result = _judgment_payload(
                action, contract, state.raw, state_yaml_path, repo_root
            )
            _persist(state_yaml_path)
            return result, 0

        # exec step: run it here and loop, so the harness never sees it.
        ok, state_yaml_path, exec_status = run_script_step(
            action, state_yaml_path=state_yaml_path, state=state,
            user_direction=user_direction,
        )
        user_direction = ""  # consumed by the step that was asking
        _persist(state_yaml_path)
        if not ok:
            return {
                "status": "error",
                "kind": KIND_EXEC,
                "step_id": step_id,
                "detail": f"exec step {step_id} failed and has no retry routing",
            }, 0
        if exec_status == "await_input":
            # The step parked on a question. Its node stays in_progress, so
            # dispatch would hand it straight back — looping here would re-run
            # the step until MAX_EXEC_BATCH instead of asking the human.
            return _awaiting_result(state_yaml_path), 0

    return {
        "status": "needs_you",
        "step_id": None,
        "detail": f"ran {MAX_EXEC_BATCH} exec steps without reaching a judgment "
                  "step — the recipe is probably not advancing",
    }, 0


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
    _persist(state_yaml_path)

    next_result, _ = step(state_yaml_path, user_direction="" if matched else text)
    return {"status": "ok", "matched": matched, "next": next_result}, 0


def _pin_config(state_raw: dict[str, Any]) -> None:
    """Point ORCHESTRATOR_CONFIG at the pack this run was seeded from."""
    if os.environ.get("ORCHESTRATOR_CONFIG"):
        return
    pack = str(state_raw.get("config_pack") or "")
    repo = str(state_raw.get("repo_root") or "") or os.environ.get("REPO_ROOT", "")
    if pack and repo:
        os.environ["ORCHESTRATOR_CONFIG"] = str(Path(repo) / ".orchestrator" / pack)
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
) -> tuple[dict[str, Any], int]:
    """Seed a run and return its identity plus the first ``step`` result."""
    from orchestrator_next.paths import WorkflowRefError, resolve_workflow_ref
    from orchestrator_next.paths import new_run_id as paths_new_run_id
    from orchestrator_next.run_store import _state_root, open_store, persist
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
    try:
        config_pack, schema, cfg_root = resolve_workflow_ref(
            recipe_ref, Path(repo_root)
        )
    except (WorkflowRefError, FileNotFoundError) as exc:
        raise ProtocolError(str(exc)) from exc
    os.environ["ORCHESTRATOR_CONFIG"] = str(cfg_root)

    run_id = paths_new_run_id()
    state_path = _state_root() / f"{run_id}.yaml"
    user_input = json.dumps(inputs, sort_keys=True) if inputs else slug
    try:
        seed_state_file(
            state_path,
            slug=slug.lower(),
            schema=schema,
            repo_root=repo_root,
            config_pack=config_pack,
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
    persist(open_store(), run_id, state_path)

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


def done(
    run_ref: str,
    step_id: str,
    *,
    out: dict[str, Any],
    usage: dict[str, Any],
    status: str = "completed",
    started_at: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Record a judgment step's structured result, then return the next step.

    Rejects the call (exit 3) when ``out`` does not satisfy the contract's
    ``out:`` block — the harness is expected to fix the step's output and
    retry, rather than have the engine record a half-finished step.
    """
    from orchestrator_next.execute import _record_with_retry

    if status not in {"completed", "abandoned"}:
        raise ProtocolError(
            f"--status must be completed or abandoned (got {status!r})"
        )

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    try:
        contract = load_contract_for_step(step_id)
    except (FileNotFoundError, ContractError, ContractNotFoundError) as exc:
        raise ProtocolError(str(exc)) from exc

    if status == "completed":
        problems = validate_out(contract, out, state.raw)
        if problems:
            raise ProtocolError(
                "out does not satisfy the step contract: " + "; ".join(problems)
            )

    outputs = dict(out)
    outputs.setdefault(
        "reason",
        f"{step_id} {status} (structured out, protocol v2)",
    )
    payload: dict[str, Any] = {
        "step_id": step_id,
        "phase": state.phase or "main",
        "status": status,
        "outputs": outputs,
        "usage": dict(usage or {}),
    }
    # When the harness knows when the step actually began, say so: record.py
    # defaults `started_at` to `now` and derives `duration_ms` from
    # `ended_at - started_at`, so a harness that omits it records every
    # judgment step at a flat 0ms however long the step really ran.
    if started_at:
        payload["started_at"] = started_at
    if isinstance(contract, AgentStepContract):
        # record.py requires `agent` on a completed agent step and enforces the
        # usage-token guard against it (docs/protocol-v2.md §5).
        payload["agent"] = _step_alias(step_id)
        # pricing.py keys its rate lookup on `usage.model` and records no cost
        # at all without one, so a harness that reports tokens but not which
        # model answered used to zero the step silently. Fall back to the model
        # the dispatcher routed this step to, and say the cost is an estimate
        # rather than a reading.
        step_usage = payload["usage"]
        if step_usage and not step_usage.get("model"):
            routed = _step_model_id(step_id)
            if routed:
                step_usage["model"] = routed
                step_usage["cost_partial"] = True

    result, code = _record_with_retry(state_yaml_path, payload)
    if code != 0:
        raise ProtocolError(
            f"record rejected the done payload: {json.dumps(result, sort_keys=True)}"
        )
    _persist(state_yaml_path)

    next_result, _ = step(state_yaml_path)
    return {
        "status": "ok",
        "step_id": step_id,
        "attempt": (result or {}).get("attempt"),
        "next": next_result,
    }, 0


def _step_alias(step_id: str) -> str:
    """The models.yaml tier alias for this step, or "" when unroutable."""
    from orchestrator_next.dispatch import _models_yaml_path
    from orchestrator_next.model_routes import resolve_step_alias

    try:
        return resolve_step_alias(step_id, None, _models_yaml_path()) or ""
    except Exception:  # noqa: BLE001 — an unroutable step still records
        return ""


def _step_model_id(step_id: str) -> str:
    """The concrete model id this step routes to, or "" when unroutable.

    The step's ``model_id`` is what the dispatcher told the harness to run, so
    it is the right thing to price against when the harness did not report
    which model actually answered.
    """
    from orchestrator_next.dispatch import _models_yaml_path
    from orchestrator_next.model_routes import resolve_route

    alias = _step_alias(step_id)
    if not alias:
        return ""
    try:
        return str(resolve_route(alias, _models_yaml_path()).get("model_id") or "")
    except Exception:  # noqa: BLE001 — an unroutable step still records
        return ""


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

    _persist(state_yaml_path)

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
# per-node metrics (what the Mod's table draws)
# ---------------------------------------------------------------------------

# The four token counts a step bills, as `record.py` writes them into
# `step_history[].usage`, mapped to the names `status --json` reports them
# under. The `usage.*` spelling is the API's (`cache_read_input_tokens`);
# the reported spelling is the table's column key.
_TOKEN_KEYS: dict[str, str] = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_input_tokens": "cache_write_tokens",
}

# Keys a step's `outputs` may carry a verdict under when no contract declares
# a `fail_on:` enum. Checked in order, so an explicit `verdict` wins.
_VERDICT_KEYS = ("verdict", "decision")

# Every numeric column the table sums into its Totals row.
_METRIC_NUMERIC_KEYS = (
    "seconds",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
)


def _num(value: Any) -> float:
    """``value`` as a float, or 0.0 for anything non-numeric (incl. bool-free)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _entry_seconds(entry: dict[str, Any]) -> float:
    """One attempt's wall time in seconds.

    ``usage.duration_ms`` is what ``record.py`` derives from the entry's own
    ``started_at``/``ended_at`` (record.py's ``duration_ms`` block), so prefer
    it; fall back to re-deriving from the stamps for an entry written before
    that, and to 0.0 when neither parses.
    """
    usage = entry.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    duration_ms = usage.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        return max(0.0, float(duration_ms) / 1000.0)
    try:
        started = _dt.datetime.fromisoformat(
            str(entry.get("started_at") or "").replace("Z", "+00:00")
        )
        ended = _dt.datetime.fromisoformat(
            str(entry.get("ended_at") or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (ended - started).total_seconds())


def _enum_out_names(step_id: str) -> tuple[frozenset[str], frozenset[str]]:
    """``(names of enum outs, names carrying a ``fail_on:``)`` for a step.

    A missing or unparseable contract yields two empty sets rather than
    raising: a metrics projection must never take `status` down.
    """
    try:
        contract = load_contract_for_step(step_id)
    except Exception:  # noqa: BLE001 — metrics are informational
        return frozenset(), frozenset()
    declared = getattr(contract, "outputs", None) or {}
    enums: set[str] = set()
    fail_on: set[str] = set()
    for name, spec in declared.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("type") == "enum":
            enums.add(str(name))
        if isinstance(spec.get("fail_on"), list):
            fail_on.add(str(name))
    return frozenset(enums), frozenset(fail_on)


def _entry_verdict(entry: dict[str, Any], step_id: str) -> str:
    """The verdict one attempt reported, or "".

    Prefers an out the contract declared as an enum with ``fail_on:`` (the
    same declaration routing reads — see ``record.failing_verdict``), then any
    enum out, then a plain ``verdict``/``decision`` key for a step whose
    contract declares nothing.
    """
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return ""
    enums, fail_on = _enum_out_names(step_id)
    for names in (fail_on, enums):
        for name in sorted(names):
            value = outputs.get(name)
            if isinstance(value, str) and value:
                return value
    for key in _VERDICT_KEYS:
        value = outputs.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def node_metrics(step_history: list[Any]) -> dict[str, dict[str, Any]]:
    """Per-step-id metrics folded out of ``step_history``.

    One row per step the history touched, with every attempt of that step
    folded in: the counts and ``seconds`` **sum** across attempts (a step
    retried twice really did bill twice), while ``model`` and ``verdict`` take
    the **last** attempt's (what the step finally ran as, and finally said).

    ``cost_partial`` is true when any attempt billed tokens the engine could
    not price — ``record.py`` stamps it when a model has no pricing row — so a
    reader knows the cost is a floor rather than a total.

    Pure: takes the raw history list, touches no state and no store.
    """
    rows: dict[str, dict[str, Any]] = {}
    for raw in step_history or []:
        entry = raw if isinstance(raw, dict) else getattr(raw, "raw", None)
        if not isinstance(entry, dict):
            continue
        step_id = str(entry.get("step_id") or "")
        if not step_id:
            continue
        usage = entry.get("usage")
        usage = usage if isinstance(usage, dict) else {}

        row = rows.setdefault(step_id, {
            "attempts": 0,
            "model": "",
            "verdict": "",
            "seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
            "cost_partial": False,
        })

        row["attempts"] += 1
        model = usage.get("model")
        if isinstance(model, str) and model:
            row["model"] = model
        verdict = _entry_verdict(entry, step_id)
        if verdict:
            row["verdict"] = verdict
        row["seconds"] += _entry_seconds(entry)
        for usage_key, column in _TOKEN_KEYS.items():
            row[column] += int(_num(usage.get(usage_key)))
        row["cost_usd"] += _num(usage.get("cost_usd"))
        if usage.get("cost_partial") is True:
            row["cost_partial"] = True

    for row in rows.values():
        row["seconds"] = round(row["seconds"], 3)
        row["cost_usd"] = round(row["cost_usd"], 6)
    return rows


def metrics_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The Totals row: every numeric column summed over ``rows``.

    ``cost_partial`` rides along so the table can mark a total that is a floor
    rather than the real spend.
    """
    totals: dict[str, Any] = {key: 0 for key in _METRIC_NUMERIC_KEYS}
    totals["cost_partial"] = False
    for row in rows:
        for key in _METRIC_NUMERIC_KEYS:
            totals[key] += _num(row.get(key))
        if row.get("cost_partial") is True:
            totals["cost_partial"] = True
    totals["seconds"] = round(totals["seconds"], 3)
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        totals[key] = int(totals[key])
    return totals


# ---------------------------------------------------------------------------
# verbs: status / events
# ---------------------------------------------------------------------------
def status(run_ref: str) -> tuple[dict[str, Any], int]:
    """Report nodes, usage totals, and gate state for a run."""
    from orchestrator_next import gates
    from orchestrator_next.parser import phase_nodes
    from orchestrator_next.pricing import sum_cost_usd

    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    _pin_config(state.raw)

    attempts: dict[str, int] = {}
    for entry in state.step_history:
        if entry.attempt:
            attempts[entry.step_id] = max(attempts.get(entry.step_id, 0), int(entry.attempt))

    # Per-node model/verdict/duration/tokens/cost, so a reader drawing a table
    # does not have to fold `events --json` itself.
    metrics = node_metrics([entry.raw for entry in state.step_history])

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
            row = metrics.get(step_id, {})
            nodes.append({
                "id": step_id,
                "phase": phase,
                "kind": (KIND_GATE if gates.node_is_gate(node)
                         else _kind_of(step_id)),
                "status": str(node.get("status") or "pending"),
                # `attempts` stays the contract's own max-attempt number; the
                # metrics row counts history entries, which agree for a normal
                # run and differ only for a history written without `attempt`.
                "attempts": attempts.get(step_id, 0) or int(row.get("attempts", 0)),
                "artifacts": node_artifacts,
                "model": row.get("model", ""),
                "verdict": row.get("verdict", ""),
                "seconds": row.get("seconds", 0.0),
                "input_tokens": row.get("input_tokens", 0),
                "output_tokens": row.get("output_tokens", 0),
                "cache_read_tokens": row.get("cache_read_tokens", 0),
                "cache_write_tokens": row.get("cache_write_tokens", 0),
                "cost_usd": row.get("cost_usd", 0.0),
                "cost_partial": row.get("cost_partial", False),
            })
            for a in node_artifacts:
                all_artifacts.append({**a, "step_id": step_id})

    totals = {"input_tokens": 0, "output_tokens": 0}
    for entry in state.step_history:
        u = entry.usage if isinstance(entry.usage, dict) else {}
        for key in totals:
            value = u.get(key)
            if isinstance(value, (int, float)):
                totals[key] += int(value)
    try:
        cost = round(sum_cost_usd(state.raw), 6)
    except Exception:  # noqa: BLE001 — pricing is informational
        cost = 0.0

    return {
        "run_id": str(state.raw.get("run_id") or Path(state_yaml_path).stem),
        "slug": state.raw.get("slug") or state.change_id,
        "state": state_yaml_path,
        "run_status": state.raw.get("status") or "active",
        "phase": state.phase,
        "nodes": nodes,
        "artifacts": all_artifacts,   # {name, path, sha256, step_id}
        "artifacts_base": str(_artifact_base(state.raw)),
        "usage": totals,
        "cost_usd": cost,
        # The table's Totals row: every numeric column summed over the nodes
        # above, so the footer and the rows can never disagree.
        "totals": metrics_totals(nodes),
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


def recipes() -> tuple[list[dict[str, Any]], int]:
    """Every recipe the resolved pack(s) offer, for a picker to choose from.

    One row per workflow YAML found by ``paths.list_workflows`` — the same
    index ``resolve_workflow_ref`` resolves a CLI ref against, so a ``name``
    here is always startable, and ``pack`` disambiguates the ones that are not
    unique (``<pack>/<name>``). ``steps`` counts the recipe's entries and
    ``gates`` names its ``{gate: ...}`` ones; ``inputs`` is the recipe's own
    ``inputs:`` block, which tells a wizard what to ask for beyond the slug.

    Never raises for one unreadable YAML: a malformed recipe is reported with
    an ``error`` field rather than taking the whole listing down, since the
    caller is usually drawing a menu.
    """
    from orchestrator_next.paths import list_workflows

    index = list_workflows()
    out: list[dict[str, Any]] = []
    for name in sorted(index):
        for pack_name, root in sorted(index[name]):
            row: dict[str, Any] = {"name": name, "pack": pack_name}
            path = root / "workflows" / f"{name}.yaml"
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as exc:
                out.append({**row, "steps": 0, "gates": [], "inputs": {},
                            "error": str(exc)})
                continue
            if not isinstance(doc, dict):
                out.append({**row, "steps": 0, "gates": [], "inputs": {},
                            "error": "recipe is not a mapping"})
                continue
            entries = doc.get("steps") or []
            entries = entries if isinstance(entries, list) else []
            gate_ids = [
                str(entry["gate"])
                for entry in entries
                if isinstance(entry, dict) and entry.get("gate")
            ]
            inputs = doc.get("inputs") or {}
            out.append({
                **row,
                "steps": len(entries),
                "gates": gate_ids,
                "inputs": inputs if isinstance(inputs, dict) else {},
            })
    return out, 0


def runs() -> tuple[list[dict[str, Any]], int]:
    """Every live run in the store, newest state first — what `status` with no
    run reports.

    ``state list`` exists but answers a *store admin* question (it takes a
    store URL, prints a fixed-width table, and reports schema/step counts), so
    it is not what a picker can read. This is the run-identity projection the
    mod needs: the fields it would otherwise call ``status`` once per run to
    learn. A run whose state will not parse is skipped rather than raising,
    for the same reason ``recipes`` tolerates a bad YAML.
    """
    from orchestrator_next.run_store import open_store

    store = open_store()
    out: list[dict[str, Any]] = []
    for run_id in store.list_ids():
        text = store.load(run_id)
        if not text:
            continue
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(raw, dict):
            continue
        out.append({
            "run_id": str(raw.get("run_id") or run_id),
            "slug": str(raw.get("slug") or raw.get("change_id") or ""),
            "run_status": str(raw.get("status") or "active"),
            "recipe": str(raw.get("schema") or raw.get("workflow") or ""),
            "current_step": _current_step_of(raw),
        })
    out.sort(key=lambda row: (row["run_status"] != "active", row["slug"]))
    return out, 0


def _current_step_of(raw: dict[str, Any]) -> str | None:
    """The step a run stands at: the last one its history touched.

    Read off ``step_history`` rather than the plan, because a node's
    ``status`` says what happened to it, not which one the driver is on.
    """
    history = raw.get("step_history")
    if not isinstance(history, list):
        return None
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("step_id"):
            return str(entry["step_id"])
    return None


def events(
    run_ref: str,
    *,
    since: str = "",
    step: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Return step_history entries, optionally narrowed.

    ``since`` keeps the entries at or after that timestamp; ``step`` keeps only
    the attempts of one node. The pane's log panel asks for one step's attempts
    and would otherwise have to read (and parse) the whole run's history on
    every selection, which on a long run is most of a megabyte of JSON per
    keystroke.
    """
    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    out = []
    for entry in state.step_history:
        raw = dict(entry.raw)
        if step and str(raw.get("step_id") or "") != step:
            continue
        if since:
            stamp = str(raw.get("ended_at") or raw.get("started_at") or "")
            if stamp and stamp < since:
                continue
        out.append(raw)
    return out, 0


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
    """Dispatch one protocol-v2 verb. Always prints JSON; exit 3 on error."""
    args = list(argv)
    if "--json" in args:
        args.remove("--json")
    try:
        if verb == "start":
            if len(args) < 2:
                raise ProtocolError("usage: orchestrator start <recipe> <slug> "
                                    "[--inputs JSON] [--ticket-id ID] --json")
            inputs = _json_flag(args, "--inputs")
            ticket_id = _pop_flag(args, "--ticket-id") or ""
            if inputs is not None and not isinstance(inputs, dict):
                raise ProtocolError("--inputs must be a JSON object")
            result, code = start(args[0], args[1], inputs=inputs, ticket_id=ticket_id)
        elif verb == "step":
            if not args:
                raise ProtocolError("usage: orchestrator step <run> --json")
            result, code = step(args[0])
        elif verb == "done":
            if len(args) < 2:
                raise ProtocolError(
                    "usage: orchestrator done <run> <step_id> --out JSON "
                    "--usage JSON [--status completed|abandoned] "
                    "[--started-at ISO8601]"
                )
            run_ref, step_id = args[0], args[1]
            rest = args[2:]
            out = _json_flag(rest, "--out", default={})
            usage = _json_flag(rest, "--usage", default={})
            st = _pop_flag(rest, "--status") or "completed"
            started_at = _pop_flag(rest, "--started-at")
            if not isinstance(out, dict):
                raise ProtocolError("--out must be a JSON object")
            if not isinstance(usage, dict):
                raise ProtocolError("--usage must be a JSON object")
            result, code = done(
                run_ref,
                step_id,
                out=out,
                usage=usage,
                status=st,
                started_at=started_at,
            )
        elif verb == "approve":
            if len(args) < 2:
                raise ProtocolError(
                    "usage: orchestrator approve <run> <token> [--edits JSON]"
                )
            run_ref, token = args[0], args[1]
            rest = args[2:]
            edits = _json_flag(rest, "--edits")
            if edits is not None and not isinstance(edits, dict):
                raise ProtocolError("--edits must be a JSON object")
            result, code = approve(run_ref, token, edits=edits)
        elif verb == "resume":
            if len(args) < 2:
                raise ProtocolError('usage: orchestrator resume <run> "<text>" --json')
            result, code = resume(args[0], " ".join(args[1:]).strip())
        elif verb == "cancel":
            if not args:
                raise ProtocolError("usage: orchestrator cancel <run>")
            result, code = cancel(args[0])
        elif verb == "reset-step":
            if len(args) < 2:
                raise ProtocolError(
                    "usage: orchestrator reset-step <run> <step_id> --json"
                )
            result, code = reset_step(args[0], args[1])
        elif verb == "recipes":
            rows, code = recipes()
            print(json.dumps(rows, sort_keys=True, indent=2, default=str))
            return code
        elif verb == "status":
            # No run named: report every live run instead of failing. This is
            # what a picker asks first ("is anything running?"), and asking it
            # used to mean `state list`, whose fixed-width table is for a
            # human at a shell, not a caller.
            if not args:
                rows, code = runs()
                print(json.dumps(rows, sort_keys=True, indent=2, default=str))
                return code
            result, code = status(args[0])
        elif verb == "events":
            if not args:
                raise ProtocolError("usage: orchestrator events <run> "
                                    "[--since TS] [--step ID] --json")
            since = _pop_flag(args, "--since") or ""
            # Not `step`: that name is the module's own `step` verb, and
            # binding it here makes it a local for the WHOLE function, so the
            # `elif verb == "step"` branch above raises UnboundLocalError.
            step_id = _pop_flag(args, "--step") or ""
            entries, code = events(args[0], since=since, step=step_id)
            for entry in entries:
                print(json.dumps(entry, sort_keys=True, default=str))
            return code
        else:  # pragma: no cover — cli.py routes only the verbs above
            raise ProtocolError(f"unknown protocol verb: {verb}")
    except ProtocolError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return EXIT_ERROR

    print(json.dumps(result, sort_keys=True, indent=2, default=str))
    sys.stdout.flush()
    return code
