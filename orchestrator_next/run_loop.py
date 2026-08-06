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
from orchestrator_next.dispatch import ContractDispatchError, dispatch
# dispatch.py defines its own ContractDispatchError(RuntimeError); parser raises
# ContractNotFoundError(ValueError) for a missing script payload and
# ContractError(ValueError) for a malformed contract. Catch all three so any
# contract problem returns exit 3 instead of crashing the loop.
from orchestrator_next.parse_completion import parse_completion
from orchestrator_next.parser import ContractError, ContractNotFoundError, load_state
from orchestrator_next.pricing import format_cost_so_far, format_last_step_usage
from orchestrator_next.record import autocommit_state, record
from orchestrator_next.usage_adapters import ZEROED_USAGE, split_stdout

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

# Zero floor overlaid under every recorded usage dict. Derived from the adapters'
# own constant rather than hand-copied: the two drifted before (this held
# cache_read_tokens/cache_creation_tokens, which no reader consumed, so the counts
# here stayed 0 next to the adapter's real ones). Token keys only — `model` is set
# per-payload and a failed step must not carry a `cost_usd`.
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
def build_prompt(
    instruction: str,
    step_context: str,
    workflow_meta: str,
) -> str:
    """Assemble the agent prompt. Ticket body is not injected here — the
    load-ticket-context workflow step writes
    spec/changes/<id>/ticket-context.md for agents to read.

    `instruction` already carries this step's own learnings.md content, if
    any (parser.load_contract_for_step inlines it — see steps/<id>/learnings.md).
    """
    return (
        f"{instruction}\n\n{workflow_meta}\n\n"
        f"Step context:\n{step_context}\n{_COMPLETION_CONTRACT}\n"
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
# Tool invocation — faithful port of run-workflow.sh invoke_tool()
# ---------------------------------------------------------------------------
def _resolve_tool_template(tool_name: str, models_yaml: str | None) -> tuple[str, list[str]]:
    """Return (binary, args_template) for `tool_name`, resolved through the
    layered `tools:` block (D1) — same layer chain/precedence as `models:`.
    """
    return model_routes.resolve_tool_template(tool_name, models_yaml)


def _build_argv(
    tool_name: str, binary: str, template: list[str],
    prompt: str, prompt_file: str, model_id: str,
    usage_file: str = "",
) -> list[str]:
    def expand(arg: str) -> str:
        if "{usage_file}" in arg:
            return arg.replace("{usage_file}", usage_file)
        if "{prompt_file}" in arg:
            return arg.replace("{prompt_file}", prompt_file)
        if "{model_id}" in arg:
            if not model_id:
                # No silent "auto" default: an unresolved model would run an
                # unknown model and record an unpriceable cost.
                raise ContractDispatchError(
                    f"{tool_name}: no model_id resolved; check the step's "
                    f"model: alias against config/models.yaml"
                )
            return arg.replace("{model_id}", model_id)
        if arg == "{prompt}":
            return prompt
        return str(arg)

    argv = [binary] + [expand(a) for a in template]
    if len(argv) == 1:  # no template
        argv += (["-p", prompt] if tool_name in ("claude", "pi") else [prompt])
    return argv


def invoke_tool(
    tool_name: str, binary: str, template: list[str],
    prompt: str, prompt_file: str, model_id: str,
    cwd: str | None, stdout_path: Path, stderr_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
    usage_file: str = "",
) -> int:
    argv = _build_argv(tool_name, binary, template, prompt, prompt_file, model_id, usage_file=usage_file)
    env = os.environ.copy()
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items() if v is not None})
    env.setdefault("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    run_cwd = cwd if cwd and Path(cwd).is_dir() else None
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.run(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL, cwd=run_cwd, env=env)
    return proc.returncode


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


def run_agent_step(
    action: dict, *, repo_root: str, models_yaml: str,
    state_raw: dict, state_yaml_path: str, tmp_dir: Path,
) -> dict:
    """Execute one agent action; always returns a done-payload dict.

    Failure policy (LOCKED, deviates from bash): tool nonzero exit AND malformed
    COMPLETION both → `failed` payload so on_failure/max_retries retries. A bad
    parse never aborts the workflow.

    Ticket context is not fetched here — load-ticket-context (workflow config)
    writes spec/changes/<id>/ticket-context.md; agent prompts instruct reading that file.
    """
    model = action["model"]
    step_id = action["step_id"]
    started_at = action.get("started_at") or datetime.now(timezone.utc).isoformat()

    # Resolve once (D3): tool + model_id must come from the SAME chosen
    # candidate in a fallback chain, never mixed across candidates.
    route = model_routes.resolve_route(model, models_yaml)
    tool_name = route["tool"]
    if not tool_name:
        _log(f"ERROR: no route for model '{model}'")
        raise SystemExit(4)
    model_id = route["model_id"]
    binary, template = _resolve_tool_template(tool_name, models_yaml)

    meta = _workflow_meta(state_raw, state_yaml_path)
    step_context = json.dumps(action.get("step_context") or {})
    prompt = build_prompt(action.get("instruction", ""), step_context, meta)
    prompt_file = tmp_dir / f"prompt_{step_id}.txt"
    prompt_file.write_text(prompt)

    work_dir = state_raw.get("worktree_path") or repo_root
    if not Path(work_dir).is_dir():
        work_dir = repo_root

    stdout_path = tmp_dir / f"out_{step_id}.txt"
    stderr_path = tmp_dir / f"err_{step_id}.txt"
    usage_path = tmp_dir / f"usage_{step_id}.json"
    fallback_note = f"  (fallback #{route['active_index']} for {model})" if route["is_fallback"] else ""
    _log(f"  invoking {tool_name} ({binary})" + (f"  model={model_id}" if model_id else "") + fallback_note)
    agent_env = dict(action.get("env") or {})
    prompt_dir = action.get("prompt_dir") or agent_env.get("ORCHESTRATOR_PROMPT_DIR")
    if prompt_dir:
        agent_env["ORCHESTRATOR_PROMPT_DIR"] = str(prompt_dir)
    rc = invoke_tool(tool_name, binary, template, prompt, str(prompt_file),
                     model_id, work_dir, stdout_path, stderr_path,
                     extra_env=agent_env,
                     usage_file=str(usage_path))
    if rc != 0:
        stderr_tail = stderr_path.read_text(errors="replace")[-2000:] if stderr_path.exists() else ""
        _log(f"WARN: tool '{binary}' exited {rc}")
        if stderr_tail.strip():
            _log(f"  stderr: {stderr_tail.strip()}")
        return _failed_payload(action, rc)

    adapter_tool = "cursor-agent" if tool_name == "cursor" else tool_name
    norm = split_stdout(adapter_tool, stdout_path, route_model=model_id or None,
                        usage_file=str(usage_path))
    usage = {k: v for k, v in norm.items() if k != "assistant_text"}
    try:
        completion = parse_completion(norm.get("assistant_text") or "")
    except ValueError as exc:
        # LOCKED policy: malformed COMPLETION is recoverable, not fatal.
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

    For archive-completed-change: durable pre-write BEFORE running (state file
    moves), so the entry survives the relocation; returns (True, relocated_path, status).

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
    # Relocate state path if the script moved it (archive-completed-change emits
    # archive_record.archive_path when it succeeds).
    new_state_path = _relocate_after_archive(outputs, new_state_path)

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


def _relocate_after_archive(outputs, default) -> str:
    """Relocate state path when a script emits archive_record.archive_path."""
    archive_path = (outputs.get("archive_record") or {}).get("archive_path") or ""
    if not archive_path:
        return default
    repo_root = os.environ.get("REPO_ROOT", "")
    candidate = os.path.join(repo_root, archive_path, "state.yaml")
    if os.path.isfile(candidate):
        _log(f"  state relocated: {candidate}")
        return candidate
    return default


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
# State finalization
# ---------------------------------------------------------------------------
def _finalize_state(state_yaml_path: str) -> None:
    try:
        with open(state_yaml_path) as f:
            raw = yaml.safe_load(f) or {}
        raw["status"] = "completed"
        raw["next_step"] = None
        Path(state_yaml_path).write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        )
    except OSError as exc:
        _log(f"finalize_state: {exc}")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

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
    tmp_dir: Path | None = None,
    keep_tmp: bool = False,
    user_direction: str = "",
    pause_on_await_input: bool = False,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> LoopResult:
    """Shared dispatch → execute → record loop for CLI and ACP.

    When ``pause_on_await_input`` is set and a step records ``status:
    await_input``, returns ``LoopResult(code=0, awaiting_step_id=...)`` so the
    caller can collect more input and continue the same step. Ticket/feature
    runs leave ``pause_on_await_input=False`` (default) and keep looping.
    """
    import tempfile
    import shutil

    models_yaml = resolve_models_yaml(models_yaml)
    owns_tmp = tmp_dir is None
    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="orc-loop-"))

    def emit(event: str, **payload: Any) -> None:
        if on_event is not None:
            on_event(event, payload)

    try:
        try:
            from orchestrator_next.spawn_resume import apply_spawn_failure_resume
            apply_spawn_failure_resume(state_yaml_path)
        except Exception:
            pass

        while True:
            if not os.path.isfile(state_yaml_path):
                _log("Workflow complete (state archived).")
                emit("complete", archived=True)
                return LoopResult(1, state_yaml_path)

            state = load_state(state_yaml_path)
            try:
                action, code = dispatch(state, state_yaml_path)
            except (ContractDispatchError, ContractNotFoundError, ContractError) as exc:
                _log(f"Contract error: {exc}")
                emit("error", message=str(exc))
                return LoopResult(3, state_yaml_path)
            if code == 1:
                _log("Workflow complete.")
                _finalize_state(state_yaml_path)
                autocommit_state(state_yaml_path)
                emit("complete")
                return LoopResult(1, state_yaml_path)
            if code == 2:
                _log("Workflow blocked.")
                autocommit_state(state_yaml_path, push=True)
                _notify_blocked(state_yaml_path, state.raw,
                                (action or {}).get("reason") or "blocked (signoff or halt)")
                emit("blocked", reason=(action or {}).get("reason") or "blocked")
                return LoopResult(2, state_yaml_path)

            step_id = action.get("step_id", "?")

            if action.get("model"):
                if user_direction:
                    base = action.get("instruction") or ""
                    action["instruction"] = (
                        f"{base}\n\nUser direction: {user_direction}"
                        if base
                        else f"User direction: {user_direction}"
                    )
                    # Consume direction for this turn so a later step doesn't
                    # re-inject the same text.
                    user_direction = ""
                _log(f"→ {step_id}  phase={action.get('phase','main')}  "
                     f"kind=agent  model={action['model']}  attempt={action.get('attempt',1)}")
                emit("step_start", step_id=step_id, kind="agent", model=action.get("model"))
                payload = run_agent_step(
                    action, repo_root=repo_root, models_yaml=models_yaml,
                    state_raw=state.raw,
                    state_yaml_path=state_yaml_path, tmp_dir=tmp_dir,
                )
                _, rc = record(state_yaml_path, payload)
                if rc == 3:
                    _log(f"WARN: record rejected payload for {step_id} — recording failed")
                    record(state_yaml_path, _failed_payload(action, 3))
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
            elif action.get("run"):
                script_direction = ""
                if user_direction:
                    script_direction = user_direction
                    user_direction = ""
                _log(f"→ {step_id}  phase={action.get('phase','main')}  kind=script")
                emit("step_start", step_id=step_id, kind="script")
                ok, state_yaml_path, status = run_script_step(
                    action,
                    state_yaml_path=state_yaml_path,
                    state=state,
                    user_direction=script_direction,
                )
                emit("step_done", step_id=step_id, kind="script", ok=ok, status=status)
                if not ok:
                    _log("Workflow aborted: deterministic script step failed.")
                    autocommit_state(state_yaml_path, push=True)
                    emit("error", message=f"{step_id} script failed")
                    return LoopResult(3, state_yaml_path)
                paused = _maybe_pause_await_input(
                    step_id, status, pause_on_await_input, emit,
                    state_yaml_path=state_yaml_path, ok=True,
                )
                if paused is not None:
                    return paused
            else:
                _log("dispatch returned no actionable step; stopping")
                emit("idle", step_id=step_id)
                return LoopResult(3, state_yaml_path)
    finally:
        if owns_tmp and not keep_tmp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


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
        ticket = ""
        schema = "feature"
        try:
            raw = yaml.safe_load(Path(result.state_yaml_path).read_text(encoding="utf-8")) or {}
            ticket = str(raw.get("ticket_id") or raw.get("slug") or raw.get("change_id") or "")
            schema = str(raw.get("schema") or "feature")
            for entry in reversed(raw.get("step_history") or []):
                if isinstance(entry, dict) and entry.get("status") == "await_input":
                    outs = entry.get("outputs") or {}
                    if isinstance(outs, dict):
                        ask = str(outs.get("ask") or "")
                    break
        except (OSError, yaml.YAMLError):
            pass
        step = result.awaiting_step_id or "?"
        _log(f"paused: awaiting user input at step {step}")
        if ask:
            _log(f"ask: {ask}")
        hint_id = ticket or "<run_id>"
        _log(
            f'resume: orchestrator {schema} {hint_id} "<your feedback or approval>"'
        )
        print(f"run_id={hint_id}", flush=True)
        print(f"awaiting_step_id={step}", flush=True)
        if ask:
            print(f"ask: {ask}", flush=True)
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


def _resolve_active_state(
    slug: str, schema: str, repo_root: str, *, config_pack: str = ""
) -> str:
    """Newest active state for this slug/schema(/pack), or "" if none."""
    state_dir = Path(repo_root) / ".orchestrator" / slug
    if not state_dir.is_dir():
        return ""
    matches: list[Path] = []
    if config_pack:
        matches.extend(state_dir.glob(f"*_{config_pack}_{schema}_state.yaml"))
    matches.extend(state_dir.glob(f"*_{schema}_state.yaml"))
    # Prefer pack-scoped files when both exist.
    matches = sorted(set(matches))
    return str(matches[-1]) if matches else ""


def _resolve_archived_state(slug: str, repo_root: str) -> str:
    """Archived state under spec/changes/archive/ for an already-completed
    feature, or "" if none. Used by `complete` teardown when the active state
    was already archived. Mirrors orchestrator-run.sh resolve_archived_state_yaml.
    """
    archive = Path(repo_root) / "spec" / "changes" / "archive"
    direct = archive / slug / "state.yaml"
    if direct.is_file():
        return str(direct)
    for dated in sorted(archive.glob(f"*-{slug}/state.yaml")):
        if dated.is_file():
            return str(dated)
    return ""


def _write_initial_state(
    state_yaml: Path, *, slug: str, schema: str, repo_root: str,
    active: list[str], prior_path: str, config_pack: str = "",
    worktree_path: str = "",
    user_input: str = "",
    ticket_id: str = "",
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
        "schema": schema,
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
    )
    from orchestrator_next import generate_plan as _gp
    _gp.generate_plan(str(state_yaml))


def _seed_state(
    slug: str,
    schema: str,
    repo_root: str,
    *,
    config_pack: str = "",
    user_input: str = "",
) -> str:
    """Seed a state file; return its path.
    Idempotent: reuse the newest matching state yaml if present."""
    state_dir = Path(repo_root) / ".orchestrator" / slug
    patterns = []
    if config_pack:
        patterns.append(f"*_{config_pack}_{schema}_state.yaml")
    patterns.append(f"*_{schema}_state.yaml")
    existing: list[Path] = []
    for pat in patterns:
        existing.extend(state_dir.glob(pat))
    existing = sorted(set(existing))
    if existing:
        _log(f"state file exists at {existing[-1]} (idempotent skip)")
        return str(existing[-1])

    state_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    file_schema = f"{config_pack}_{schema}" if config_pack else schema
    state_yaml = state_dir / f"{timestamp}_{file_schema}_state.yaml"
    prior = sorted(state_dir.glob("*_state.yaml"))
    prior_path = str(prior[-1]) if prior else ""

    try:
        seed_state_file(
            state_yaml,
            slug=slug,
            schema=schema,
            repo_root=repo_root,
            config_pack=config_pack,
            prior_path=prior_path,
            user_input=user_input,
        )
    except FileNotFoundError as exc:
        _log(f"ERROR: {exc}")
        raise SystemExit(7) from exc
    except ValueError as exc:
        _log(f"ERROR: {exc}")
        raise SystemExit(1) from exc
    except Exception as exc:
        state_yaml.unlink(missing_ok=True)
        _log(f"error: generate_plan failed: {exc}")
        raise SystemExit(2) from exc

    _log(f"init-workflow: {slug} ({config_pack + '/' if config_pack else ''}{schema}) ready at {state_yaml}")
    return str(state_yaml)


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
        elif a in ("--help", "-h"):
            _log(
                "Usage: orchestrator run <input|run_id> […] [--schema S] [--repo PATH] "
                "[--models-config PATH] [--seed-only] [flag=value ...]\n"
                "  New run: opaque input (ticket id or free text) → prints run_id=.\n"
                "  Resume:  run_id [\"feedback\"] when state already exists."
            )
            return 7
        elif a.startswith("-"):
            _log(f"ERROR: unknown option: {a}")
            return 7
        elif "=" in a and positionals:
            if _AGENT_ROUTE_RE.match(a):
                agent_route_flags.append(a)
            else:
                flag_overrides.append(a)
        elif "=" in a and not positionals:
            # Allow flag=value before input for compatibility
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

    first = positionals[0]
    # Resume if this id already has active (or complete-schema archived) state.
    # Try exact slug and lowercase (legacy ticket dirs used lowercase).
    resume_slug = ""
    state_yaml_path = ""
    for candidate in (first, first.lower()):
        found = _resolve_active_state(
            candidate, schema, repo_root, config_pack=config_pack
        )
        if found:
            resume_slug = candidate
            state_yaml_path = found
            break
    if not state_yaml_path and schema == "complete":
        for candidate in (first, first.lower()):
            found = _resolve_archived_state(candidate, repo_root)
            if found:
                resume_slug = candidate
                state_yaml_path = found
                _log(f"Resuming complete on archived state: {state_yaml_path}")
                break

    user_direction = ""
    user_input = ""
    if state_yaml_path:
        run_id = resume_slug
        user_direction = " ".join(positionals[1:]).strip()
        _log(f"resuming run_id={run_id}")
    else:
        run_id = str(uuid.uuid4())
        user_input = " ".join(positionals).strip()
        state_yaml_path = _seed_state(
            run_id, schema, repo_root, config_pack=config_pack, user_input=user_input,
        )
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
    return run_loop(
        state_yaml_path,
        repo_root=repo_root,
        models_yaml=models_yaml,
        user_direction=user_direction,
    )


if __name__ == "__main__":
    sys.exit(run_cmd(sys.argv[1:]))
