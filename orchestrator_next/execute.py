"""Step execution and recording primitives shared by the protocol-v2 verbs.

`orchestrator step` runs exec (script) steps here and builds judgment payloads
here; `orchestrator done` records through `_record_with_retry` here. The engine
never spawns a model — a judgment step's payload goes back to the harness
(protocol-v2 principle 1).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import model_routes
from orchestrator_next.dispatch import ContractDispatchError
from orchestrator_next.pricing import format_cost_so_far, format_last_step_usage
from orchestrator_next.record import record

# The canonical usage shape. An agent runner reports usage directly, so the only
# remaining job of this constant is to floor every recorded dict.
ZEROED_USAGE: dict[str, Any] = {
    "model": "",
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}

# Token keys only — `model` is set per-payload and a failed step must not carry
# a `cost_usd`.
_EMPTY_USAGE = {k: 0 for k in ZEROED_USAGE if k.endswith("_tokens")}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", file=sys.stderr)


def _log_cost_so_far(state_yaml_path: str) -> None:
    """Emit the step's own usage, then the running `[cost so far: $X.XX]` total,
    both re-derived from live state.

    Reads the just-updated state.yaml: step_history[-1] is the step that just
    recorded, so its duration/tokens/cost render without threading usage back
    through the call sites. Best-effort — a missing or unreadable state file
    never interrupts the caller.
    """
    try:
        with open(state_yaml_path) as f:
            state_raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return
    step_usage = format_last_step_usage(state_raw)
    if step_usage:
        _log(f"  {step_usage}")
    _log(format_cost_so_far(state_raw))


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Judgment-step prompt assembly
# ---------------------------------------------------------------------------
def _structured_output_contract(
    step_id: str, out_paths: dict[str, str], out_schema: dict[str, dict]
) -> str:
    """The instructions that tell a judgment step what to write and report.

    The step writes its artifacts to the named paths and ends with one JSON
    object naming the values the contract declared. The harness lifts that
    object into ``orchestrator done --out``.
    """
    lines = [
        "\n---",
        "When you are finished, write every artifact below to its exact path, "
        "then end your output with a single fenced ```json block — nothing after it.",
    ]
    if out_paths:
        lines.append("\nArtifacts to write:")
        lines += [f"  {name}: {path}" for name, path in sorted(out_paths.items())]
    if out_schema:
        lines.append("\nValues to report in the JSON block:")
        for name, spec in sorted(out_schema.items()):
            if spec.get("type") == "enum":
                lines.append(f"  {name}: one of {spec.get('values')}")
            else:
                lines.append(f"  {name}: {spec.get('type', 'string')}")
    keys = sorted(set(out_paths) | set(out_schema))
    example = {k: (out_paths.get(k) or f"<{k}>") for k in keys}
    example["reason"] = "<why this step can advance, 1-4 sentences>"
    lines.append(
        "\nFinal block (exact shape):\n```json\n"
        + json.dumps(example, indent=2, sort_keys=True)
        + "\n```"
    )
    lines.append(
        f"\nIf {step_id} cannot complete, emit the same block with "
        '"status": "failed" and a "reason" explaining why.'
    )
    return "\n".join(lines) + "\n"


def build_prompt(
    instruction: str,
    step_context: str,
    workflow_meta: str,
    *,
    output_contract: str,
) -> str:
    """Assemble the judgment prompt.

    `instruction` already carries this step's own learnings.md content, if any
    (parser.load_contract_for_step inlines it — see steps/<id>/learnings.md).
    ``output_contract`` is the structured-output tail from
    ``_structured_output_contract``.
    """
    return (
        f"{instruction}\n\n{workflow_meta}\n\n"
        f"Step context:\n{step_context}\n{output_contract}\n"
    )


def _workflow_meta(state_raw: dict[str, Any], state_yaml_path: str) -> str:
    """Workflow-identity lines prepended to the agent prompt."""
    cid = state_raw.get("change_id") or state_raw.get("slug") or Path(state_yaml_path).parent.name
    schema = state_raw.get("schema") or ""
    repo = state_raw.get("repo_root") or ""
    wt = state_raw.get("worktree_path") or ""
    lines = [
        f"Workflow: change_id={cid} schema={schema} repo_root={repo}",
        f"state_yaml_path={state_yaml_path}",
    ]
    if wt:
        lines.append(f"worktree_path={wt}")
    return "\n".join(lines)


def build_agent_payload(
    action: dict, *, repo_root: str, models_yaml: str,
    state_raw: dict, state_yaml_path: str,
    output_contract: str,
) -> dict:
    """Build the judgment payload handed back to the harness.

    `model` is the tier alias resolved to a concrete route by `model_routes`;
    the harness decides what to do with it.
    """
    route = model_routes.resolve_route(action["model"], models_yaml)
    if not route["model_id"]:
        # An exhausted fallback chain resolves to nothing. Handing a blank model
        # to the harness would run an unknown model at an unpriceable cost.
        _log(f"ERROR: no route for model '{action['model']}'")
        raise SystemExit(4)
    work_dir = state_raw.get("worktree_path") or repo_root
    if not Path(work_dir).is_dir():
        work_dir = repo_root

    meta = _workflow_meta(state_raw, state_yaml_path)
    step_context = json.dumps(action.get("step_context") or {})
    env = dict(action.get("env") or {})
    prompt_dir = action.get("prompt_dir") or env.get("ORCHESTRATOR_PROMPT_DIR")
    if prompt_dir:
        env["ORCHESTRATOR_PROMPT_DIR"] = str(prompt_dir)

    return {
        "step_id": action["step_id"],
        "phase": action.get("phase", "main"),
        "model": action["model"],
        "model_id": route["model_id"],
        "prompt": build_prompt(
            action.get("instruction", ""), step_context, meta,
            output_contract=output_contract,
        ),
        "cwd": work_dir,
        "env": env,
    }


# ---------------------------------------------------------------------------
# Script (exec) step execution
# ---------------------------------------------------------------------------
def run_script_step(
    action: dict,
    *,
    state_yaml_path: str,
    state=None,
    user_direction: str = "",
) -> tuple[bool, str, str | None]:
    """Run an inline script step. Returns (ok, new_state_path, recorded_status).

    ok=False  → script exited nonzero AND step has no on_failure routing: the
    workflow must abort (deterministic scripts like merge-to-main / create-worktree
    cannot self-heal by re-dispatch). ok=False is returned ONLY when re-dispatch
    would loop; if the contract has on_failure, the failure is recorded and the
    caller may retry (ok=True).

    recorded_status is the status written to step_history (``completed``,
    ``await_input``, ``failed``, …), or None when nothing was recorded.

    For state-mutating steps (archive-completed-change): durable pre-write
    BEFORE running, so the entry survives even if the script itself fails
    partway through.

    ``user_direction`` is exposed as ``ORCHESTRATOR_USER_DIRECTION`` for scripts
    that emit ``await_input`` and need the next resume text.
    """
    from orchestrator_next.parser import (
        ScriptStepContract,
        load_contract_for_step,
        load_state,
    )
    from orchestrator_next.paths import config_root
    from orchestrator_next.step_env import inline_script_env
    step_id = action["step_id"]
    phase = action.get("phase", "main")
    attempt = action.get("attempt", 1)
    if state is None:
        state = load_state(state_yaml_path)
    contract = load_contract_for_step(step_id)
    if not isinstance(contract, ScriptStepContract):
        raise ContractDispatchError(f"run_script_step called on non-script contract: {step_id}")
    action_env = dict(action.get("env") or {})
    if user_direction:
        action_env["ORCHESTRATOR_USER_DIRECTION"] = user_direction
    env = inline_script_env(state, state_yaml_path, action_env=action_env)
    # parser absolutizes run: relative to the contract dir, so the script's own
    # directory IS the step dir.
    env["ORCHESTRATOR_STEP_DIR"] = os.path.dirname(contract.run)
    _params_path = config_root() / "steps" / step_id / "contract.yaml"
    if _params_path.is_file():
        _raw = yaml.safe_load(_params_path.read_text(encoding="utf-8")) or {}
        for k, v in (_raw.get("params") or {}).items():
            env.setdefault(str(k), str(v))
    if not os.path.isfile(contract.run):
        raise FileNotFoundError(f"step script not found: {contract.run}")
    run_cmd = ["bash", contract.run]

    _log(f"→ {step_id}  phase={phase}  kind=inline script  attempt={attempt}")
    _log(f"  run: {' '.join(run_cmd)}")

    state_mutating = contract.state_mutating
    if state_mutating:
        record(state_yaml_path, {
            "step_id": step_id, "phase": phase, "attempt": attempt,
            "status": "completed",
            "outputs": {
                "reason": "recorded pre-script (state-mutating inline step)",
            },
            "evidence": {"summary": "recorded pre-script (state-mutating inline step)"},
        })

    cwd = env.get("REPO_ROOT") or None
    if cwd and not os.path.isdir(cwd):
        cwd = None
    # Measure elapsed ms here: record.py's derive-from-timestamps fallback can't
    # serve script steps (started_at would default to ended_at), and _utcnow_iso
    # truncates to whole seconds, flooring sub-second steps to 0.
    _start_ms = _now_ms()
    proc = subprocess.run(run_cmd, capture_output=True, env=env, cwd=cwd)
    script_duration_ms = _now_ms() - _start_ms
    if proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
        sys.stderr.buffer.flush()

    new_state_path = state_yaml_path
    if proc.returncode != 0:
        if not state_mutating:
            record(state_yaml_path, {
                "step_id": step_id, "phase": phase, "attempt": attempt,
                "status": "failed",
                "outputs": {"reason": f"script exited {proc.returncode}"},
                "usage": {"duration_ms": script_duration_ms},
                "evidence": {"summary": f"script exited {proc.returncode}"},
            })
        _log(f"✗ {step_id}  failed  script_exit={proc.returncode}")
        # No script step carries on_failure routing (every on_failure source/
        # target in the schemas is an agent step). A failed deterministic script
        # can't self-heal via re-dispatch, so abort. ok=False signals that.
        return False, new_state_path, "failed" if not state_mutating else None

    parsed = _parse_stdout_outputs(proc)
    status, outputs = _script_status_and_outputs(parsed)
    if not state_mutating:
        if status != "await_input" and (
            not isinstance(outputs.get("reason"), str) or not str(outputs.get("reason")).strip()
        ):
            outputs = {**outputs, "reason": "inline script completed"}
        payload = {
            "step_id": step_id, "phase": phase, "attempt": attempt,
            "status": status, "outputs": outputs,
            "usage": {"duration_ms": script_duration_ms},
            "evidence": {"outputs": outputs, "summary": f"inline script {status}"},
        }
        if isinstance(parsed.get("state_patch"), dict):
            payload["state_patch"] = parsed["state_patch"]
        elif isinstance(outputs.get("state_patch"), dict):
            payload["state_patch"] = outputs["state_patch"]
        record(state_yaml_path, payload)

    _log(f"✓ {step_id}  done  status={status}")
    _log_cost_so_far(new_state_path)
    return True, new_state_path, status if not state_mutating else "completed"


def _script_status_and_outputs(parsed: dict) -> tuple[str, dict]:
    """Split script stdout JSON into (status, outputs).

    Accepts either the structured form ``{status, outputs: {...}}`` or a flat
    outputs dict (status defaults to completed).
    """
    if not isinstance(parsed, dict):
        return "completed", {}
    raw_status = parsed.get("status")
    raw_outputs = parsed.get("outputs")
    if isinstance(raw_status, str) and isinstance(raw_outputs, dict):
        return raw_status, dict(raw_outputs)
    if isinstance(raw_status, str):
        return raw_status, {
            k: v for k, v in parsed.items() if k not in ("status", "state_patch")
        }
    return "completed", dict(parsed)


def _parse_stdout_outputs(proc) -> dict:
    """Parse the last JSON line of script stdout into an outputs dict."""
    lines = proc.stdout.decode(errors="replace").strip().splitlines()
    if lines:
        try:
            return json.loads(lines[-1])
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------
def discard_scratch(state_raw: dict) -> bool:
    """Delete the run's scratch dir: artifacts survive, scratch does not.

    Returns True when a directory was removed. Never raises — a run that
    finished should not fail on cleanup of a throwaway directory.
    """
    import shutil

    from orchestrator_next.paths import scratch_dir

    try:
        path = scratch_dir(state_raw)
    except (OSError, ValueError):
        return False
    if not path.is_dir():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def _finalize_state(state_yaml_path: str) -> None:
    """Mark a finished run completed, then drop its scratch dir."""
    try:
        with open(state_yaml_path) as f:
            raw = yaml.safe_load(f) or {}
        raw["status"] = "completed"
        raw["next_step"] = None
        path = Path(state_yaml_path)
        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    except OSError as exc:
        _log(f"finalize_state: {exc}")
        return
    from orchestrator_next.record import _persist_if_materialized
    _persist_if_materialized(path, raw)
    discard_scratch(raw)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
DEFAULT_MAX_PARALLEL = 4
_RECORD_CONFLICT_RETRIES = 6


def max_parallel() -> int:
    """Steps to run concurrently. `ORCHESTRATOR_MAX_PARALLEL=1` restores serial."""
    raw = os.environ.get("ORCHESTRATOR_MAX_PARALLEL")
    if not raw:
        return DEFAULT_MAX_PARALLEL
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_PARALLEL


def _record_with_retry(state_yaml_path: str, payload: dict) -> tuple[dict, int]:
    """record(), retried when another worker won the compare-and-swap.

    Two workers finishing at once both read-modify-write the same run. The
    store refuses the loser (exit 4, `state_write_conflict`) rather than letting
    it clobber — so the loser simply re-reads and re-applies. Its own step
    outcome is unaffected by whatever the winner wrote, because appending a
    history entry and flipping one node's status commute.
    """
    delay = 0.02
    for attempt in range(_RECORD_CONFLICT_RETRIES):
        result, rc = record(state_yaml_path, payload)
        if rc != 4 or (result or {}).get("reason") != "state_write_conflict":
            return result, rc
        time.sleep(delay)
        delay = min(delay * 2, 0.5)
        if attempt == _RECORD_CONFLICT_RETRIES - 2:
            _log(f"WARN: record contention on {payload.get('step_id')} — retrying")
    _log(f"ERROR: could not record {payload.get('step_id')} after "
         f"{_RECORD_CONFLICT_RETRIES} conflict retries")
    return {"reason": "state_write_conflict"}, 4


# ---------------------------------------------------------------------------
# models.yaml resolution
# ---------------------------------------------------------------------------
# Process-lifetime cache: models.yaml path keyed by (env, config root hint).
_models_yaml_resolved: str | None = None
_models_yaml_cache_key: str = ""


def resolve_models_yaml(explicit: str = "", *, repo_root: str = "") -> str:
    """Resolve models.yaml once per process (or when env/config root changes)."""
    global _models_yaml_resolved, _models_yaml_cache_key
    env_override = os.environ.get("ORCHESTRATOR_MODELS_CONFIG", "")
    cfg = os.environ.get("ORCHESTRATOR_CONFIG", "")
    key = f"{explicit}|{env_override}|{cfg}|{repo_root}"
    if _models_yaml_resolved is not None and key == _models_yaml_cache_key:
        return _models_yaml_resolved

    path = ""
    if explicit and Path(explicit).is_file():
        path = explicit
    elif env_override and Path(env_override).is_file():
        path = env_override
    else:
        candidates: list[Path] = []
        if cfg:
            candidates.append(Path(cfg) / "models.yaml")
        try:
            from orchestrator_next.dispatch import _models_yaml_path
            p = _models_yaml_path()
            if p:
                candidates.append(Path(p))
        except Exception:  # noqa: BLE001
            pass
        if repo_root:
            root = Path(repo_root)
            candidates.extend([
                root / ".orchestrator" / "config" / "models.yaml",
                root / "config" / "models.yaml",
            ])
        for cand in candidates:
            if cand.is_file():
                path = str(cand)
                break

    _models_yaml_cache_key = key
    _models_yaml_resolved = path
    return path


# ---------------------------------------------------------------------------
# await_input resume routing
#
# A step that records `status: await_input` parks the run and leaves an
# `awaiting: {step_id, ask, options}` block on the state (record.py). These two
# functions turn the user's answer back into a state transition without
# re-dispatching the step. No protocol-v2 verb calls them yet — `approve` is
# for gate tokens, which is a different mechanism — so they are currently
# reachable only by an embedder. Kept rather than deleted because record.py
# still produces the block they consume.
# ---------------------------------------------------------------------------
def match_awaiting_option(text: str, options: list[dict]) -> dict | None:
    """Exact label match, label's first word, or 1-based option number."""
    norm = (text or "").strip().lower()
    if not norm:
        return None
    if norm.isdigit():
        idx = int(norm) - 1
        if 0 <= idx < len(options):
            return options[idx]
        return None
    for opt in options:
        label = str(opt.get("label") or "").strip().lower()
        if not label:
            continue
        if norm == label or norm == label.split()[0]:
            return opt
    return None


def route_awaiting_input(state_yaml_path: str, user_direction: str) -> bool:
    """Apply a matched await_input option to the run; return True when applied.

    Returns False — leaving the state untouched — when the run is not awaiting
    input, offers no options, or the text matches none of them. The caller then
    re-dispatches the step with the raw text for the agent to interpret.
    """
    from orchestrator_next import readiness
    from orchestrator_next.record import (
        _find_workflow_node,
        _persist_if_materialized,
        _state_from_raw,
    )

    try:
        raw = yaml.safe_load(Path(state_yaml_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    awaiting = raw.get("awaiting")
    if not isinstance(awaiting, dict):
        return False
    options = awaiting.get("options")
    if not isinstance(options, list) or not options:
        return False
    opt = match_awaiting_option(user_direction, options)
    if opt is None:
        return False

    step_id = str(awaiting.get("step_id") or "")
    phase = str(raw.get("phase") or "main")
    label = str(opt.get("label") or "")
    reset_to = str(opt.get("reset_to") or "").strip()
    raw.pop("awaiting", None)

    if reset_to:
        from orchestrator_next.reset_step import apply_dag_reset
        apply_dag_reset(raw, phase, reset_to, keep_history_for=step_id)
        raw["status"] = "active"
        _log(f"awaiting-input: user selected {label!r} → reset to {reset_to!r}")
    else:
        node = _find_workflow_node(raw, phase, step_id)
        if node is not None:
            node["status"] = "completed"
        history = list(raw.get("step_history") or [])
        history.append({
            "step_id": step_id, "phase": phase, "status": "completed",
            "outputs": {"reason": f"user selected: {label}"},
            "attempt": len(
                [h for h in history if isinstance(h, dict) and h.get("step_id") == step_id]
            ) + 1,
        })
        raw["step_history"] = history
        nxt = readiness.next_ready_node(_state_from_raw(raw))
        raw["next_step"] = {"phase": phase, "step_id": nxt} if nxt else None
        _log(f"awaiting-input: user selected {label!r} → advance")

    path = Path(state_yaml_path)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _persist_if_materialized(path, raw)
    discard_scratch(raw)
    return True
