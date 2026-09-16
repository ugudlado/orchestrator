"""In-process dispatch loop for `orchestrator run`.

Replaces the bash drivers (run-workflow.sh, orchestrator-run.sh, seed-state.sh's
shell glue). `orchestrator run <id>` drives every step in-process: dispatch →
execute (agent|script) → record → repeat. One canonical path per step kind.

Exit codes (protocol, unchanged):
  1 complete · 2 blocked · 3 contract/parse error · 4 unknown agent route ·
  6 tool subprocess failure (recorded) · 7 unexpected/usage.
"""
from __future__ import annotations

import json
import copy
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import model_routes
from orchestrator_next.dispatch import ContractDispatchError, dispatch_batch
# dispatch.py defines its own ContractDispatchError(RuntimeError); parser raises
# ContractNotFoundError(ValueError) for a missing script payload and
# ContractError(ValueError) for a malformed contract. Catch all three so any
# contract problem returns exit 3 instead of crashing the loop.
from orchestrator_next.parse_completion import parse_completion
from orchestrator_next.parser import ContractError, ContractNotFoundError, load_state
from orchestrator_next.pricing import format_cost_so_far, format_last_step_usage
from orchestrator_next.record import _find_workflow_node, record

_COMPLETION_CONTRACT = """
---
You MUST end your stdout with a COMPLETION: block. Fields must be indented under COMPLETION: with two spaces — do NOT write them at column 0 and do NOT wrap in code fences.

IMPORTANT: Output values are parsed as YAML. If a value contains a colon (:), quote the entire value with double quotes.

Every outcome requires outputs.reason (non-empty): advance (completed/recovered) explains what happened and why the step can move forward; go-back (failed/abandoned) explains why the workflow must retry or stop.

On status completed/recovered, also emit an evidence: block under outputs: `verified` maps 1:1 to this step prompt's `## Verify` items (each a check you ran and its real result — never fabricated); `decisions` lists non-obvious choices made and why (may be empty for mechanical steps).

Advance form:
COMPLETION:
  step_id: <this-step-id>
  status: completed
  outputs:
    key: value
    evidence:
      verified:
        - check: "<a ## Verify item, run verbatim>"
          result: "<real output>"
      decisions:
        - "<non-obvious choice and why>"
    reason: >
      <what was done, how, and why this step can advance — 1-4 sentences>

Go-back form (no evidence required — the step did not complete):
COMPLETION:
  step_id: <this-step-id>
  status: failed
  outputs:
    reason: "why we go back or stop (quote if the reason contains a colon)"
"""

# The canonical usage shape. Moved here from the deleted usage_adapters.py when
# vendor-spawn transports went away: an agent runner reports usage directly, so
# the only remaining job of this constant is to floor every recorded dict.
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
    never interrupts the loop.
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
# Prompt assembly — faithful port of run-workflow.sh build_prompt()
# ---------------------------------------------------------------------------
def _structured_output_contract(
    step_id: str, out_paths: dict[str, str], out_schema: dict[str, dict]
) -> str:
    """The protocol-v2 replacement for the COMPLETION block.

    A migrated step writes its artifacts to the named paths and ends with one
    JSON object naming the values the contract declared. The harness lifts that
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
    output_contract: str | None = None,
) -> str:
    """Assemble the agent prompt. Ticket body is not injected here — the
    load-ticket-context workflow step writes
    spec/changes/<id>/ticket-context.md for agents to read.

    `instruction` already carries this step's own learnings.md content, if
    any (parser.load_contract_for_step inlines it — see steps/<id>/learnings.md).

    ``output_contract`` overrides the trailing COMPLETION contract: a step that
    declares ``out:`` gets the protocol-v2 structured-output instructions
    instead (Phase 1.3). Omitted → the legacy COMPLETION block, unchanged.
    """
    tail = _COMPLETION_CONTRACT if output_contract is None else output_contract
    return (
        f"{instruction}\n\n{workflow_meta}\n\n"
        f"Step context:\n{step_context}\n{tail}\n"
    )


def _workflow_meta(state_raw: dict[str, Any], state_yaml_path: str) -> str:
    """Reproduce state_inspect workflow-meta lines used in the agent prompt."""
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


# ---------------------------------------------------------------------------
# Agent step execution
# ---------------------------------------------------------------------------
def _failed_payload(action: dict, exit_code: int) -> dict:
    # usage carries model="none" + zero tokens so dispatch._is_spawn_failure
    # counts this toward the spawn-failure cap (quality_bar.max_spawn_failures).
    # Termination of a failing step is handled by record's on_failure routing +
    # retry cap (and, with no routing, a failed node simply isn't re-opened — the
    # phase completes). The spawn-cap shape here is the belt-and-suspenders bound
    # for steps that DO loop via on_failure: without model="none" those retries
    # wouldn't count as spawn failures and could outrun the cap. See
    # test_run_loop_termination.
    # outputs.reason is required on every recorded outcome.
    reason = (
        "malformed COMPLETION"
        if exit_code == 5
        else f"tool exit {exit_code}"
    )
    return {
        "step_id": action["step_id"],
        "phase": action.get("phase", "main"),
        "status": "failed",
        "agent": action.get("model", ""),
        "outputs": {
            "reason": reason,
            "task_execution_result": {"status": "failed", "exit_code": exit_code},
        },
        "usage": {**dict(_EMPTY_USAGE), "model": "none"},
    }


_RESERVED_PAYLOAD_KEYS = frozenset({
    "step_id", "phase", "status", "agent", "agent_id", "attempt",
    "started_at", "usage", "outputs", "evidence", "state_patch",
})


def _agent_payload(action: dict, completion: dict, usage: dict, started_at) -> dict:
    payload = dict(completion)
    payload["step_id"] = action["step_id"]
    payload["phase"] = action.get("phase", "main")
    payload["agent"] = action.get("model", "")
    if not isinstance(payload.get("outputs"), dict):
        payload["outputs"] = {}
    for key in list(payload.keys()):
        if key not in _RESERVED_PAYLOAD_KEYS and key not in payload["outputs"]:
            payload["outputs"][key] = payload.pop(key)
    payload["usage"] = {**dict(_EMPTY_USAGE), **(usage or {})}
    if started_at:
        payload["started_at"] = started_at
    return payload


class NoAgentRunnerError(RuntimeError):
    """Raised when a judgment step is dispatched with no agent runner installed."""


def _no_agent_runner(_payload: dict) -> dict:
    raise NoAgentRunnerError(
        "no agent runner: use the harness protocol (Phase 1.1) or --headless "
        "(Phase 1.5)"
    )


# The single seam between the engine and whatever executes a model. The engine
# never spawns a vendor CLI (protocol-v2 principle 1): the harness installs a
# runner here, headless mode installs its own, and tests inject a fake. A runner
# takes the judgment payload built by `run_agent_step` and returns
# `{"assistant_text": str, **usage}`.
AGENT_RUNNER: Callable[[dict], dict] = _no_agent_runner


def build_agent_payload(
    action: dict, *, repo_root: str, models_yaml: str,
    state_raw: dict, state_yaml_path: str,
    output_contract: str | None = None,
) -> dict:
    """Build the judgment payload handed to the agent runner.

    `model` is the tier alias resolved to a concrete route by `model_routes`;
    the runner decides what to do with it.

    ``output_contract`` is the protocol-v2 structured-output tail for a
    migrated step; omitted, the prompt keeps the legacy COMPLETION block.
    """
    route = model_routes.resolve_route(action["model"], models_yaml)
    if not route["tool"] and not route["model_id"]:
        # An exhausted fallback chain resolves to nothing. Handing a blank model
        # to the runner would run an unknown model at an unpriceable cost, so
        # this stays a hard exit (4) as it was under the vendor-spawn path.
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


def run_agent_step(
    action: dict, *, repo_root: str, models_yaml: str,
    state_raw: dict, state_yaml_path: str,
) -> dict:
    """Execute one agent action via AGENT_RUNNER; always returns a done-payload.

    Failure policy (LOCKED): a runner error AND a malformed COMPLETION both →
    `failed` payload, so on_failure/max_retries retries. A bad parse never
    aborts the workflow. `NoAgentRunnerError` is the one exception: no runner
    installed is a configuration fault, not a step failure, so it propagates.
    """
    step_id = action["step_id"]
    started_at = action.get("started_at") or datetime.now(timezone.utc).isoformat()

    payload = build_agent_payload(
        action, repo_root=repo_root, models_yaml=models_yaml,
        state_raw=state_raw, state_yaml_path=state_yaml_path,
    )
    _log(f"  running {step_id} via agent runner"
         + (f"  model={payload['model_id']}" if payload["model_id"] else ""))

    try:
        result = AGENT_RUNNER(payload) or {}
    except NoAgentRunnerError:
        raise
    except Exception as exc:  # noqa: BLE001
        _log(f"WARN: agent runner failed for {step_id}: {exc}")
        # 6 = runner failure, keeping the exit-code vocabulary the
        # spawn-failure cap already counts.
        return _failed_payload(action, 6)

    usage = {k: v for k, v in result.items() if k != "assistant_text"}
    try:
        completion = parse_completion(result.get("assistant_text") or "")
    except ValueError as exc:
        _log(f"WARN: malformed COMPLETION for {step_id} — recording failed (retryable): {exc}")
        return _failed_payload(action, 5)

    return _agent_payload(action, completion, usage, started_at)



# ---------------------------------------------------------------------------
# Script step execution — canonical path (lifted from bin/orchestrator inline
# arm; dead exit-10 soft-fail intentionally NOT carried).
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
    cannot self-heal by re-dispatch). Matches the old CLI inline arm's exit(3).
    ok=False is returned ONLY when re-dispatch would loop; if the contract has
    on_failure, the failure is recorded and the loop retries (ok=True).

    recorded_status is the status written to step_history (``completed``,
    ``await_input``, ``failed``, …), or None when nothing was recorded.

    For state-mutating steps (archive-completed-change): durable pre-write
    BEFORE running, so the entry survives even if the script itself fails
    partway through.

    ``user_direction`` is exposed as ``ORCHESTRATOR_USER_DIRECTION`` for scripts
    that emit ``await_input`` and need the next resume text (ACP parity with agents).
    """
    from orchestrator_next.parser import ScriptStepContract, load_contract_for_step
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
        # can't self-heal via re-dispatch, so abort — matches the old CLI inline
        # arm's sys.exit(3). ok=False signals the loop to stop.
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

    Accepts either the structured form ``{status, outputs: {...}}`` or the
    legacy flat outputs dict (status defaults to completed).
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
# Exit-2 notification — unattended runs need a human channel for signoffs
# ---------------------------------------------------------------------------
def _notify_blocked(state_yaml_path: str, state_raw: dict[str, Any], reason: str) -> None:
    """Pipe a JSON blocked-event to ORCHESTRATOR_NOTIFY_CMD (stdin), if set.

    Channel-agnostic: point the env var at curl / a Slack CLI / anything.
    Best-effort — a failing notifier never changes the exit code.
    """
    cmd = os.environ.get("ORCHESTRATOR_NOTIFY_CMD", "")
    if not cmd:
        return
    schema = state_raw.get("schema")
    if isinstance(schema, dict):
        schema = schema.get("type")
    payload = json.dumps({
        "event": "blocked",
        "change_id": state_raw.get("change_id") or Path(state_yaml_path).parent.name,
        "schema": schema or "",
        "reason": reason,
        "state_yaml_path": state_yaml_path,
    })
    try:
        subprocess.run(["bash", "-c", cmd], input=payload, text=True,
                       capture_output=True, timeout=30)
    except Exception as exc:  # noqa: BLE001 — notification is best-effort
        _log(f"WARN: notify command failed: {exc}")


# ---------------------------------------------------------------------------
# await_input option routing — deterministic resume when user text matches
# a label the awaiting step offered (see record.py's awaiting/options block).
# ---------------------------------------------------------------------------
def _match_awaiting_option(text: str, options: list[dict]) -> dict | None:
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


def _route_awaiting_input(state_yaml_path: str, user_direction: str) -> bool:
    """If state is awaiting input with options and user_direction matches one,
    apply it deterministically and return True (caller loops again without
    re-dispatching the step). No match, no awaiting block, or no options →
    return False and the step re-runs with the raw text (agent interprets it).
    """
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
    opt = _match_awaiting_option(user_direction, options)
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
        from orchestrator_next.record import _state_from_raw
        from orchestrator_next import readiness
        nxt = readiness.next_ready_node(_state_from_raw(raw))
        raw["next_step"] = {"phase": phase, "step_id": nxt} if nxt else None
        _log(f"awaiting-input: user selected {label!r} → advance")

    path = Path(state_yaml_path)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    from orchestrator_next.record import _persist_if_materialized
    _persist_if_materialized(path, raw)
    discard_scratch(raw)


def discard_scratch(state_raw: dict) -> bool:
    """Delete the run's scratch dir (Phase 2.4): artifacts survive, scratch does not.

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


# ---------------------------------------------------------------------------
# State finalization
# ---------------------------------------------------------------------------
def _finalize_state(state_yaml_path: str) -> None:
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
    # Phase 2.4: a finished run keeps its artifacts and drops its scratch.
    discard_scratch(raw)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _state_exists(state_yaml_path: str) -> bool:
    """Is this run still addressable? A file run disappears when archived; a
    stored run does not, so `exists` is the backend's question, not os.path's."""
    from orchestrator_next import state_store

    try:
        store, handle = state_store.open_store(state_yaml_path)
        return store.exists(handle)
    except (ValueError, OSError):
        return False


def _finish_agent_step(state_yaml_path: str, action: dict, payload: dict) -> None:
    """Record one agent step's outcome, tolerating write contention."""
    result, rc = _record_with_retry(state_yaml_path, payload)
    if rc == 3:
        _log(f"WARN: record rejected payload for {action['step_id']} — recording failed")
        _record_with_retry(state_yaml_path, _failed_payload(action, 3))
        return
    if rc == 4:
        _log(f"WARN: could not record {action['step_id']} — state write conflict")
        return
    _log(f"✓ {action['step_id']}  done  status={payload.get('status','completed')}")
    _log_cost_so_far(state_yaml_path)


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


# Exit codes (unchanged for CLI): 1 complete · 2 blocked · 3 error.
# drive_loop also uses 0 = paused for await_input (ACP multi-turn only).
LOOP_PAUSED = 0


@dataclass(frozen=True)
class LoopResult:
    """Result of one drive_loop invocation (CLI or ACP)."""
    code: int
    state_yaml_path: str
    awaiting_step_id: str | None = None


# Process-lifetime cache: models.yaml path keyed by (env, config root mtime hint).
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


def _maybe_pause_await_input(
    step_id: str,
    status: str | None,
    pause_on_await_input: bool,
    emit: Callable[..., None],
    *,
    state_yaml_path: str,
    ok: bool,
) -> LoopResult | None:
    """Return LOOP_PAUSED / error LoopResult when status is await_input; else None."""
    if status != "await_input" or not ok:
        return None
    if pause_on_await_input:
        emit("await_input", step_id=step_id)
        return LoopResult(LOOP_PAUSED, state_yaml_path, awaiting_step_id=step_id)
    _log(
        f"ERROR: step {step_id} returned await_input but "
        "pause_on_await_input is off — refusing to spin"
    )
    emit("error", message=f"{step_id} await_input without pause support")
    return LoopResult(3, state_yaml_path)


def drive_loop(
    state_yaml_path: str,
    *,
    repo_root: str,
    models_yaml: str = "",
    user_direction: str = "",
    pause_on_await_input: bool = False,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> LoopResult:
    """Shared dispatch → execute → record loop.

    When ``pause_on_await_input`` is set and a step records ``status:
    await_input``, returns ``LoopResult(code=0, awaiting_step_id=...)`` so the
    caller can collect more input and continue the same step. Ticket/feature
    runs leave ``pause_on_await_input=False`` (default) and keep looping.
    """
    models_yaml = resolve_models_yaml(models_yaml)

    def emit(event: str, **payload: Any) -> None:
        if on_event is not None:
            on_event(event, payload)

    while True:
        if not _state_exists(state_yaml_path):
            _log("Workflow complete (state archived).")
            emit("complete", archived=True)
            return LoopResult(1, state_yaml_path)

        if user_direction and _route_awaiting_input(state_yaml_path, user_direction):
            user_direction = ""
            continue

        try:
            actions, code = dispatch_batch(
                state_yaml_path, max_parallel=max_parallel()
            )
        except (ContractDispatchError, ContractNotFoundError, ContractError) as exc:
            _log(f"Contract error: {exc}")
            emit("error", message=str(exc))
            return LoopResult(3, state_yaml_path)
        state = load_state(state_yaml_path)
        action = actions[0] if actions else {}
        if code == 1:
            _log("Workflow complete.")
            _finalize_state(state_yaml_path)
            emit("complete")
            return LoopResult(1, state_yaml_path)
        if code == 2:
            _log("Workflow blocked.")
            _notify_blocked(state_yaml_path, state.raw,
                            (action or {}).get("reason") or "blocked (signoff or halt)")
            emit("blocked", reason=(action or {}).get("reason") or "blocked")
            return LoopResult(2, state_yaml_path)

        agent_actions = [a for a in actions if a.get("model")]
        script_actions = [a for a in actions if not a.get("model") and a.get("run")]

        # Script steps stay strictly serial. They mutate the checkout
        # (worktree create/remove, merge, archive) and several relocate the
        # state handle itself — running two at once is not a concurrency
        # problem, it is a correctness one.
        for act in script_actions:
            step_id = act.get("step_id", "?")
            script_direction = ""
            if user_direction:
                script_direction = user_direction
                user_direction = ""
            _log(f"→ {step_id}  phase={act.get('phase','main')}  kind=script")
            emit("step_start", step_id=step_id, kind="script")
            ok, state_yaml_path, status = run_script_step(
                act,
                state_yaml_path=state_yaml_path,
                state=state,
                user_direction=script_direction,
            )
            emit("step_done", step_id=step_id, kind="script", ok=ok, status=status)
            if not ok:
                _log("Workflow aborted: deterministic script step failed.")
                emit("error", message=f"{step_id} script failed")
                return LoopResult(3, state_yaml_path)
            paused = _maybe_pause_await_input(
                step_id, status, pause_on_await_input, emit,
                state_yaml_path=state_yaml_path, ok=True,
            )
            if paused is not None:
                return paused

        if len(agent_actions) == 1:
            act = agent_actions[0]
            step_id = act.get("step_id", "?")
            if user_direction:
                base = act.get("instruction") or ""
                act["instruction"] = (
                    f"{base}\n\nUser direction: {user_direction}"
                    if base
                    else f"User direction: {user_direction}"
                )
                # Consume direction for this turn so a later step doesn't
                # re-inject the same text.
                user_direction = ""
            _log(f"→ {step_id}  phase={act.get('phase','main')}  "
                 f"kind=agent  model={act['model']}  attempt={act.get('attempt',1)}")
            emit("step_start", step_id=step_id, kind="agent", model=act.get("model"))
            payload = run_agent_step(
                act, repo_root=repo_root, models_yaml=models_yaml,
                state_raw=state.raw,
                state_yaml_path=state_yaml_path,
            )
            _, rc = _record_with_retry(state_yaml_path, payload)
            if rc == 3:
                _log(f"WARN: record rejected payload for {step_id} — recording failed")
                _record_with_retry(state_yaml_path, _failed_payload(act, 3))
            else:
                _log(f"✓ {step_id}  done  status={payload.get('status','completed')}")
                _log_cost_so_far(state_yaml_path)
            status = payload.get("status")
            emit("step_done", step_id=step_id, kind="agent",
                 status=status, rc=rc)
            paused = _maybe_pause_await_input(
                step_id, status, pause_on_await_input, emit,
                state_yaml_path=state_yaml_path, ok=(rc == 0),
            )
            if paused is not None:
                return paused

        elif len(agent_actions) > 1:
            _log(f"→ {len(agent_actions)} steps in parallel: "
                 + ", ".join(a["step_id"] for a in agent_actions))
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _run(act: dict) -> tuple[dict, dict]:
                # Each worker gets its own state_raw copy: run_agent_step
                # only reads it, and sharing one dict across threads would
                # be a data race waiting to happen.
                return act, run_agent_step(
                    act, repo_root=repo_root, models_yaml=models_yaml,
                    state_raw=copy.deepcopy(state.raw),
                    state_yaml_path=state_yaml_path,
                )

            awaiting_ids: list[str] = []
            with ThreadPoolExecutor(max_workers=len(agent_actions)) as pool:
                futures = [pool.submit(_run, a) for a in agent_actions]
                for fut in as_completed(futures):
                    act, payload = fut.result()
                    # Recorded as each finishes, not batched at the end:
                    # a crash mid-batch must not lose the steps that landed.
                    _finish_agent_step(state_yaml_path, act, payload)
                    emit("step_done", step_id=act.get("step_id", "?"),
                         kind="agent", status=payload.get("status"))
                    if payload.get("status") == "await_input":
                        awaiting_ids.append(act.get("step_id", "?"))
            if awaiting_ids:
                # Pause only after the whole batch has drained and recorded.
                paused = _maybe_pause_await_input(
                    awaiting_ids[0], "await_input", pause_on_await_input, emit,
                    state_yaml_path=state_yaml_path, ok=True,
                )
                if paused is not None:
                    return paused

        elif not actions:
            _log("dispatch returned no actionable step; continuing")


def run_loop(
    state_yaml_path: str,
    *,
    repo_root: str,
    models_yaml: str,
    user_direction: str = "",
) -> int:
    """CLI entry — exit 0 paused (await_input), 1 complete, 2 blocked, 3 error."""
    result = drive_loop(
        state_yaml_path,
        repo_root=repo_root,
        models_yaml=models_yaml,
        user_direction=user_direction,
        pause_on_await_input=True,
    )
    if result.code == LOOP_PAUSED:
        ask = ""
        options: list = []
        ticket = ""
        schema = "feature"
        try:
            raw = yaml.safe_load(Path(result.state_yaml_path).read_text(encoding="utf-8")) or {}
            ticket = str(raw.get("ticket_id") or raw.get("slug") or raw.get("change_id") or "")
            schema = str(raw.get("schema") or "feature")
            awaiting = raw.get("awaiting")
            if isinstance(awaiting, dict):
                ask = str(awaiting.get("ask") or "")
                if isinstance(awaiting.get("options"), list):
                    options = awaiting["options"]
        except (OSError, yaml.YAMLError):
            pass
        step = result.awaiting_step_id or "?"
        _log(f"paused: awaiting user input at step {step}")
        if ask:
            _log(f"ask: {ask}")
        hint_id = ticket or "<run_id>"
        for i, opt in enumerate(options, start=1):
            label = str((opt or {}).get("label") or "")
            _log(f"  {i}. {label}")
        example = str((options[0] or {}).get("label") or "your feedback or approval") if options else "your feedback or approval"
        _log(
            f'resume: orchestrator {schema} {hint_id} "{example}"'
            + ("  (or a number 1-{})".format(len(options)) if options else "")
        )
        print(f"run_id={hint_id}", flush=True)
        print(f"awaiting_step_id={step}", flush=True)
        if ask:
            print(f"ask: {ask}", flush=True)
        for i, opt in enumerate(options, start=1):
            print(f"option_{i}: {(opt or {}).get('label') or ''}", flush=True)
        return LOOP_PAUSED
    return result.code


# ---------------------------------------------------------------------------
# `orchestrator run` entry — arg parse + seeding + loop (replaces both shells)
# ---------------------------------------------------------------------------
_AGENT_ROUTE_RE = re.compile(r"^agent\.[a-zA-Z0-9_-]+\.(tool|model)=")


def _build_route_overrides(flags: list[str]) -> str:
    data: dict[str, dict[str, str]] = {}
    pat = re.compile(r"agent\.([a-zA-Z0-9_-]+)\.(tool|model)=(.+)")
    for flag in flags:
        m = pat.fullmatch(flag)
        if m:
            data.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    return json.dumps(data)


def _new_run_id() -> str:
    from orchestrator_next.paths import new_run_id

    return new_run_id()


def _pack_sha_for(schema: str, repo_root: str) -> str:
    """Identify the pack this run was seeded from (best effort)."""
    from orchestrator_next.paths import (
        ConfigRootError,
        WorkflowRefError,
        config_root,
        pack_sha,
        resolve_workflow_ref,
    )

    try:
        _pack, _wf, cfg = resolve_workflow_ref(
            schema, Path(repo_root) if repo_root else None
        )
    except (WorkflowRefError, OSError):
        try:
            cfg = config_root()
        except (ConfigRootError, OSError):
            return ""
    try:
        return pack_sha(cfg)
    except OSError:
        return ""


def _write_initial_state(
    state_yaml: Path, *, slug: str, schema: str, repo_root: str,
    active: list[str], prior_path: str, config_pack: str = "",
    worktree_path: str = "",
    user_input: str = "",
    ticket_id: str = "",
    run_id: str = "",
) -> None:
    """Write the initial state.yaml, carrying identity fields from the most
    recent prior state file when provided.

    ``slug`` / ``change_id`` are the run identity (UUID for new opaque-input runs).
    ``user_input`` is opaque text for the workflow (ticket id or brief) — never
    used as identity. ``ticket_id`` is only set when explicitly provided (or
    carried from prior); it is not defaulted from slug.
    """
    prior_context: dict = {}
    if prior_path:
        try:
            prior_raw = yaml.safe_load(Path(prior_path).read_text()) or {}
            for key in ("worktree_path", "branch", "repo_root", "change_id", "slug",
                        "ticket_id", "config_pack", "user_input"):
                if prior_raw.get(key):
                    prior_context[key] = prior_raw[key]
        except (OSError, yaml.YAMLError):
            pass  # prior unreadable — start fresh

    repo_root = prior_context.get("repo_root") or repo_root
    config_pack = config_pack or prior_context.get("config_pack") or ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = prior_context.get("slug") or slug
    state = {
        "change_id": prior_context.get("change_id") or slug,
        "slug": slug,
        "run_id": run_id or _new_run_id(),
        "schema": schema,
        "recipe": schema,
        "status": "active",
        "repo_root": repo_root,
        "workflow_plan": {"main": {"active": active, "filtered": []}},
        "phase": "main",
        "next_step": {"phase": "main", "step_id": active[0]},
        "step_history": [],
        "created_at": now,
        "started_at": now,
    }
    tid = (ticket_id or prior_context.get("ticket_id") or "").strip()
    if tid:
        state["ticket_id"] = tid
    ui = (user_input or prior_context.get("user_input") or "").strip()
    if ui:
        state["user_input"] = ui
    if config_pack:
        state["config_pack"] = config_pack
    sha = _pack_sha_for(schema, repo_root)
    if sha:
        state["pack_sha"] = sha
    wt = worktree_path or prior_context.get("worktree_path") or ""
    if wt:
        state["worktree_path"] = wt
    if prior_context.get("branch"):
        state["branch"] = prior_context["branch"]

    state_yaml.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
    _log(f"seeded: {state_yaml}")


def _schema_active_steps(schema: str, repo_root: str = "") -> list[str]:
    """Load step ids for a workflow schema (pack-aware when possible)."""
    from orchestrator_next.paths import WorkflowRefError, config_root, resolve_workflow_ref
    from orchestrator_next.workflow_steps import step_id_of

    schema_yaml: Path | None = None
    try:
        root = Path(repo_root) if repo_root else None
        _, wf, cfg = resolve_workflow_ref(schema, root)
        cand = cfg / "workflows" / f"{wf}.yaml"
        if cand.is_file():
            schema_yaml = cand
    except WorkflowRefError:
        pass
    if schema_yaml is None:
        schema_yaml = config_root() / "workflows" / f"{schema}.yaml"
    if not schema_yaml.is_file():
        raise FileNotFoundError(f"schema '{schema}' not found: {schema_yaml}")
    schema_doc = yaml.safe_load(schema_yaml.read_text(encoding="utf-8")) or {}
    active = [
        sid
        for entry in schema_doc.get("steps", [])
        if (sid := step_id_of(entry))
    ]
    if not active:
        raise ValueError(f"schema '{schema}' declares no steps")
    return active


def seed_state_file(
    state_yaml: Path,
    *,
    slug: str,
    schema: str,
    repo_root: str,
    worktree_path: str = "",
    config_pack: str = "",
    prior_path: str = "",
    user_input: str = "",
    ticket_id: str = "",
    run_id: str = "",
) -> None:
    """Seed ``state_yaml`` and run generate_plan (shared by ticket + session paths)."""
    active = _schema_active_steps(schema, repo_root)
    state_yaml.parent.mkdir(parents=True, exist_ok=True)
    _write_initial_state(
        state_yaml,
        slug=slug,
        schema=schema,
        repo_root=repo_root,
        active=active,
        prior_path=prior_path,
        config_pack=config_pack,
        worktree_path=worktree_path,
        user_input=user_input,
        ticket_id=ticket_id,
        run_id=run_id,
    )
    from orchestrator_next import generate_plan as _gp
    _gp.generate_plan(str(state_yaml))


def run_cmd(argv: list[str]) -> int:
    """`orchestrator run <input…> [--schema S] [--repo P] …`

    Engine stays dumb about ticket vs free text: positional args are opaque.
    New runs mint a UUID ``run_id`` as change_id/slug and store the text as
    ``user_input`` for the workflow. If the first positional already has active
    state for this schema, that run is resumed (remaining args = user_direction).
    """
    from orchestrator_next.models_config_cli import consume_models_config_argv

    argv = consume_models_config_argv(argv)

    schema_ref = "feature"
    repo_arg = ""
    seed_only = False
    ticket_id_arg = ""
    flag_overrides: list[str] = []
    agent_route_flags: list[str] = []
    routes_override_arg = ""
    positionals: list[str] = []

    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--schema":
            schema_ref = args.pop(0)
        elif a == "--repo":
            repo_arg = args.pop(0)
        elif a == "--routes-override":
            routes_override_arg = args.pop(0)
        elif a == "--seed-only":
            seed_only = True
        elif a == "--ticket-id":
            ticket_id_arg = args.pop(0) if args else ""
        elif a in ("--help", "-h"):
            _log(
                "Usage: orchestrator run <input|run_id> […] [--schema S] [--repo PATH] "
                "[--models-config PATH] [--ticket-id ID] [--seed-only] [flag=value ...]\n"
                "  New run: opaque input (ticket id or free text) → prints run_id=.\n"
                "  Resume:  run_id [\"feedback\"] when state already exists."
            )
            return 7
        elif a.startswith("-"):
            _log(f"ERROR: unknown option: {a}")
            return 7
        elif "=" in a:
            # Allowed both before and after the positional input.
            if _AGENT_ROUTE_RE.match(a):
                agent_route_flags.append(a)
            else:
                flag_overrides.append(a)
        else:
            positionals.append(a)

    if not positionals:
        _log(
            'Usage: orchestrator run <input|run_id> […]  '
            'e.g. orchestrator feature "add login"  or  orchestrator feature ORC-1'
        )
        return 7

    # No repo-side marker file required — any git repo (or cwd) is runnable;
    # ticketing and conventions are env-/docs-driven.
    repo_root = os.path.abspath(repo_arg) if repo_arg else os.environ.get("REPO_ROOT", "")
    if not repo_root:
        try:
            repo_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
            ).stdout.strip() or os.getcwd()
        except Exception:
            repo_root = os.getcwd()
    os.environ["REPO_ROOT"] = repo_root

    from orchestrator_next.paths import WorkflowRefError, resolve_workflow_ref
    try:
        config_pack, schema, cfg_root = resolve_workflow_ref(
            schema_ref, Path(repo_root)
        )
    except WorkflowRefError as exc:
        _log(f"ERROR: {exc}")
        return 7
    os.environ["ORCHESTRATOR_CONFIG"] = str(cfg_root)

    if routes_override_arg:
        os.environ["ORCHESTRATOR_ROUTES_YAML"] = os.path.abspath(routes_override_arg)
    if agent_route_flags:
        os.environ["ORCHESTRATOR_MODEL_ROUTE_OVERRIDES"] = _build_route_overrides(agent_route_flags)

    from orchestrator_next.run_store import _state_root, materialize, open_store, persist

    store = open_store()

    first = positionals[0]
    # Resume if this id (or its lowercase form) is a known LIVE run_id in the store.
    resume_run_id = ""
    for candidate in (first, first.lower()):
        if store.load(candidate) is not None:
            resume_run_id = candidate
            break
    # Not live — an archived (already-completed) run_id still resolves so the
    # `complete` teardown schema (or a status check) can operate on it, but
    # doesn't re-seed a fresh run under the same id.
    archived_run_id = ""
    if not resume_run_id:
        for candidate in (first, first.lower()):
            if candidate in store.list_ids(archived=True):
                archived_run_id = candidate
                break

    user_direction = ""
    user_input = ""
    if archived_run_id and not resume_run_id:
        run_id = archived_run_id
        _log(f"run_id={run_id} is completed (archived)")
        print(f"run_id={run_id}", flush=True)
        print("status=completed (archived)", flush=True)
        return 1
    if resume_run_id:
        run_id = resume_run_id
        user_direction = " ".join(positionals[1:]).strip()
        _log(f"resuming run_id={run_id}")
        state_yaml_path = str(materialize(store, run_id, repo_root=repo_root))
    else:
        run_id = str(uuid.uuid4())
        user_input = " ".join(positionals).strip()
        run_slug = ticket_id_arg.strip().lower() or run_id
        state_path = _state_root() / f"{run_id}.yaml"
        state_yaml_path = str(state_path)
        try:
            seed_state_file(
                state_path,
                slug=run_slug, schema=schema, repo_root=repo_root,
                config_pack=config_pack, user_input=user_input,
                ticket_id=ticket_id_arg,
            )
        except FileNotFoundError as exc:
            _log(f"ERROR: {exc}")
            return 7
        except ValueError as exc:
            _log(f"ERROR: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            state_path.unlink(missing_ok=True)
            _log(f"error: generate_plan failed: {exc}")
            return 2
        persist(store, run_id, state_yaml_path)
        print(f"run_id={run_id}", flush=True)
        _log(f"started run_id={run_id} user_input={user_input[:120]!r}")

    if seed_only:
        _log(f"seeded (seed-only): {state_yaml_path}")
        print(state_yaml_path)
        return 0

    ref_label = f"{config_pack}/{schema}" if config_pack else schema
    _log(f"Running workflow: run_id={run_id} schema={ref_label} state={state_yaml_path}")
    if user_direction:
        _log(f"user_direction: {user_direction[:200]}")
    models_yaml = resolve_models_yaml(repo_root=repo_root)
    if models_yaml and os.environ.get("ORCHESTRATOR_MODELS_CONFIG"):
        _log(f"models override: {models_yaml}")

    if not store.lock(run_id):
        _log(f"ERROR: run {run_id} is busy — another process is driving it")
        return 7
    try:
        code = run_loop(
            state_yaml_path,
            repo_root=repo_root,
            models_yaml=models_yaml,
            user_direction=user_direction,
        )
    finally:
        store.unlock(run_id)
    if code == 1:
        store.archive(run_id)
        _log(f"archived: run_id={run_id}")
    return code


if __name__ == "__main__":
    sys.exit(run_cmd(sys.argv[1:]))
