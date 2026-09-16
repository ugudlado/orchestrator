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
    from orchestrator_next.run_loop import (
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

    # A migrated step (declares out:) gets the structured-output tail; an
    # unmigrated one keeps the legacy COMPLETION block (protocol v2 §10).
    output_contract = (
        _structured_output_contract(action["step_id"], out_paths, out_schema)
        if contract.outputs
        else None
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
                "show": gates.preview(show, _show_paths(state_raw, show)),
                "token_name": approve_as,
            },
            "token": record["token"],
        },
    }


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
def step(run_ref: str) -> tuple[dict[str, Any], int]:
    """Advance the run: execute consecutive exec steps, stop at judgment/gate.

    Returns ``(result, exit_code)``. ``result['status']`` is one of
    ``ready|running|done|blocked|needs_you|error`` (protocol v2 §3).
    """
    from orchestrator_next.dispatch import (
        EXIT_GATE_REQUIRED,
        ContractDispatchError,
        dispatch,
    )
    from orchestrator_next.run_loop import run_script_step

    state_yaml_path = resolve_run(run_ref)
    repo_root = os.environ.get("REPO_ROOT", "") or os.getcwd()

    for _ in range(MAX_EXEC_BATCH):
        if not Path(state_yaml_path).is_file():
            return {"status": "done", "step_id": None,
                    "detail": "run archived"}, 0
        state = load_state(state_yaml_path)
        _pin_config(state.raw)
        try:
            action, code = dispatch(state, state_yaml_path)
        except (ContractDispatchError, ContractNotFoundError, ContractError) as exc:
            return {"status": "error", "step_id": None, "detail": str(exc)}, 0
        except FileNotFoundError as exc:
            return {"status": "error", "step_id": None, "detail": str(exc)}, 0

        if code == 1:
            return {"status": "done", "step_id": None}, 0
        if code == 2:
            return {
                "status": "blocked",
                "kind": None,
                "step_id": (action or {}).get("step_id"),
                "detail": (action or {}).get("reason") or "blocked (signoff or halt)",
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
            result = _judgment_payload(
                action, contract, state.raw, state_yaml_path, repo_root
            )
            _persist(state_yaml_path)
            return result, 0

        # exec step: run it here and loop, so the harness never sees it.
        ok, state_yaml_path, _status = run_script_step(
            action, state_yaml_path=state_yaml_path, state=state
        )
        _persist(state_yaml_path)
        if not ok:
            return {
                "status": "error",
                "kind": KIND_EXEC,
                "step_id": step_id,
                "detail": f"exec step {step_id} failed and has no retry routing",
            }, 0

    return {
        "status": "needs_you",
        "step_id": None,
        "detail": f"ran {MAX_EXEC_BATCH} exec steps without reaching a judgment "
                  "step — the recipe is probably not advancing",
    }, 0


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
    from orchestrator_next.run_loop import seed_state_file
    from orchestrator_next.run_store import _state_root, open_store, persist

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
) -> tuple[dict[str, Any], int]:
    """Record a judgment step's structured result, then return the next step.

    Rejects the call (exit 3) when ``out`` does not satisfy the contract's
    ``out:`` block — the harness is expected to fix the step's output and
    retry, rather than have the engine record a half-finished step.
    """
    from orchestrator_next.run_loop import _record_with_retry

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


def events(run_ref: str, *, since: str = "") -> tuple[list[dict[str, Any]], int]:
    """Return step_history entries, optionally those at or after ``since``."""
    state_yaml_path = resolve_run(run_ref)
    state = load_state(state_yaml_path)
    out = []
    for entry in state.step_history:
        raw = dict(entry.raw)
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
                    "--usage JSON [--status completed|abandoned]"
                )
            run_ref, step_id = args[0], args[1]
            rest = args[2:]
            out = _json_flag(rest, "--out", default={})
            usage = _json_flag(rest, "--usage", default={})
            st = _pop_flag(rest, "--status") or "completed"
            if not isinstance(out, dict):
                raise ProtocolError("--out must be a JSON object")
            if not isinstance(usage, dict):
                raise ProtocolError("--usage must be a JSON object")
            result, code = done(run_ref, step_id, out=out, usage=usage, status=st)
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
        elif verb == "cancel":
            if not args:
                raise ProtocolError("usage: orchestrator cancel <run>")
            result, code = cancel(args[0])
        elif verb == "status":
            if not args:
                raise ProtocolError("usage: orchestrator status <run> --json")
            result, code = status(args[0])
        elif verb == "events":
            if not args:
                raise ProtocolError("usage: orchestrator events <run> "
                                    "[--since TS] --json")
            since = _pop_flag(args, "--since") or ""
            entries, code = events(args[0], since=since)
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
