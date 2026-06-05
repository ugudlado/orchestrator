"""In-process dispatch loop for `orchestrator run`.

Replaces the bash drivers (run-workflow.sh, orchestrator-run.sh, seed-state.sh's
shell glue). `orchestrator run <id>` drives every step in-process: dispatch →
execute (agent|script) → record → repeat. One canonical path per step kind.

Seeding reuses the existing Python helpers (seed_parse_overrides, seed_write_state,
generate_plan) — the shell only ever orchestrated them.

Exit codes (ORC-45 protocol, unchanged):
  1 complete · 2 blocked · 3 contract/parse error · 4 unknown agent route ·
  6 tool subprocess failure (recorded) · 7 unexpected/usage.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import agent_routes
from orchestrator_next.agent_overlay import overlay_text
from orchestrator_next.dispatch import ContractDispatchError, dispatch
# dispatch.py defines its own ContractDispatchError(RuntimeError) but the
# missing-run path raises parser.ContractDispatchError(ValueError) — a DIFFERENT
# class. Catch both so a contract error returns exit 3 instead of crashing.
from orchestrator_next.parser import ContractDispatchError as ParserContractDispatchError
from orchestrator_next.parser import load_state
from orchestrator_next.record import record
from orchestrator_next.usage_adapters import split_stdout

_LIB = Path(__file__).resolve().parent / "scripts" / "lib"


def _import_by_path(mod_name: str, filename: str):
    """Import a hyphenated/script-dir module (parse-completion.py) by path."""
    spec = importlib.util.spec_from_file_location(mod_name, _LIB / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_parse_completion_mod = _import_by_path("parse_completion", "parse-completion.py")
parse_completion = _parse_completion_mod.parse_completion

_COMPLETION_CONTRACT = """
---
You MUST end your stdout with a COMPLETION: block. Fields must be indented under COMPLETION: with two spaces — do NOT write them at column 0 and do NOT wrap in code fences.

IMPORTANT: Output values are parsed as YAML. If a value contains a colon (:), quote the entire value with double quotes.

Success form:
COMPLETION:
  step_id: <this-step-id>
  status: completed
  outputs:
    key: value

Failure/skip form:
COMPLETION:
  step_id: <this-step-id>
  status: abandoned
  outputs:
    reason: "why this step could not complete (quote if the reason contains a colon)"
"""

_EMPTY_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
}

_STATE_MUTATING_INLINE_STEPS = {"archive-completed-change"}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", file=sys.stderr)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Prompt assembly — faithful port of run-workflow.sh build_prompt()
# ---------------------------------------------------------------------------
def build_prompt(
    instruction: str,
    step_context: str,
    ticket_context: str,
    workflow_meta: str,
    ticket_id: str,
) -> str:
    if ticket_context:
        return (
            f"{instruction}\n\n{workflow_meta}\n\n"
            f"Ticket / bug report ({ticket_id}):\n{ticket_context}\n\n"
            f"Step context:\n{step_context}\n{_COMPLETION_CONTRACT}\n"
        )
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


def _fetch_ticket_context(ticket_id: str, repo_root: str) -> str:
    """Fetch ticket body for agent steps (backlog backend only, as in bash)."""
    if not ticket_id:
        return ""
    project_yaml = Path(repo_root) / "spec" / "project.yaml"
    backend = "backlog"
    try:
        data = yaml.safe_load(project_yaml.read_text()) or {}
        backend = (data.get("ticketing") or "backlog").strip()
    except OSError:
        pass
    if backend != "backlog":
        return ""
    try:
        out = subprocess.run(
            ["backlog", "task", "view", ticket_id, "--plain"],
            cwd=repo_root, capture_output=True, text=True,
        )
        return out.stdout if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------------------
# Tool invocation — faithful port of run-workflow.sh invoke_tool()
# ---------------------------------------------------------------------------
def _resolve_tool_template(tool_name: str, agents_yaml: str | None) -> tuple[str, list[str]]:
    """Return (binary, args_template) from the tools: block of agents.yaml."""
    binary, template = tool_name, []
    if agents_yaml and Path(agents_yaml).is_file():
        cfg = yaml.safe_load(Path(agents_yaml).read_text()) or {}
        entry = (cfg.get("tools") or {}).get(tool_name) or {}
        binary = entry.get("binary") or tool_name
        template = entry.get("args_template") or []
    return binary, template


def _pi_settings() -> dict[str, Any]:
    path = Path.home() / ".pi" / "agent" / "settings.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _build_argv(
    tool_name: str, binary: str, template: list[str],
    prompt: str, prompt_file: str, model_tier: str,
) -> list[str]:
    def expand(arg: str) -> str:
        if "{prompt_file}" in arg:
            return arg.replace("{prompt_file}", prompt_file)
        if "{model_tier}" in arg:
            return arg.replace("{model_tier}", model_tier or "auto")
        if arg == "{prompt}":
            return prompt
        return str(arg)

    argv = [binary] + [expand(a) for a in template]
    if len(argv) == 1:  # no template
        argv += (["-p", prompt] if tool_name in ("claude", "pi") else [prompt])

    # Pi: inject saved provider/model flags when template hasn't supplied them.
    if tool_name == "pi" and "--provider" not in argv:
        settings = _pi_settings()
        flags: list[str] = []
        for field, flag in (("provider", "--provider"), ("model", "--model"), ("thinking", "--thinking")):
            if settings.get(field):
                flags += [flag, str(settings[field])]
        argv = [argv[0]] + flags + argv[1:]
    return argv


def invoke_tool(
    tool_name: str, binary: str, template: list[str],
    prompt: str, prompt_file: str, model_tier: str,
    cwd: str | None, stdout_path: Path, stderr_path: Path,
) -> int:
    argv = _build_argv(tool_name, binary, template, prompt, prompt_file, model_tier)
    env = os.environ.copy()
    env.setdefault("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    run_cwd = cwd if cwd and Path(cwd).is_dir() else None
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.run(argv, stdout=out, stderr=err, cwd=run_cwd, env=env)
    return proc.returncode


# ---------------------------------------------------------------------------
# Agent step execution
# ---------------------------------------------------------------------------
def _failed_payload(action: dict, exit_code: int, duration_ms: int) -> dict:
    # usage carries model="none" + zero tokens so dispatch._is_spawn_failure
    # counts this toward the spawn-failure cap (quality_bar.max_spawn_failures).
    # Termination of a failing step is handled by record's on_failure routing +
    # retry cap (and, with no routing, a failed node simply isn't re-opened — the
    # phase completes). The spawn-cap shape here is the belt-and-suspenders bound
    # for steps that DO loop via on_failure: without model="none" those retries
    # wouldn't count as spawn failures and could outrun the cap. See
    # test_run_loop_termination.
    return {
        "step_id": action["step_id"],
        "phase": action.get("phase", "main"),
        "status": "failed",
        "agent": action.get("agent", ""),
        "outputs": {"task_execution_result": {"status": "failed", "exit_code": exit_code}},
        "usage": {**dict(_EMPTY_USAGE), "model": "none"},
        "duration_ms": duration_ms,
    }


def _agent_payload(action: dict, completion: dict, usage: dict, started_at, duration_ms: int) -> dict:
    payload = dict(completion)
    payload["step_id"] = action["step_id"]
    payload["phase"] = action.get("phase", "main")
    payload["agent"] = action.get("agent", "")
    if not isinstance(payload.get("outputs"), dict):
        payload["outputs"] = {}
    for key in ("learn_result", "phase_review_report", "discovery_result"):
        if key in payload and key not in payload["outputs"]:
            payload["outputs"][key] = payload.pop(key)
    payload["usage"] = {**dict(_EMPTY_USAGE), **(usage or {})}
    if started_at:
        payload["started_at"] = started_at
    payload["duration_ms"] = duration_ms
    return payload


def run_agent_step(
    action: dict, *, repo_root: str, agents_yaml: str, ticket_id: str,
    state_raw: dict, state_yaml_path: str, tmp_dir: Path,
) -> dict:
    """Execute one agent action; always returns a done-payload dict.

    Failure policy (LOCKED, deviates from bash): tool nonzero exit AND malformed
    COMPLETION both → `failed` payload so on_failure/max_retries retries. A bad
    parse never aborts the workflow.
    """
    agent = action["agent"]
    step_id = action["step_id"]
    started_at = action.get("started_at") or datetime.now(timezone.utc).isoformat()
    start_ms = _now_ms()

    tool_name = agent_routes.resolve_subprocess(agent, agents_yaml)
    if not tool_name:
        _log(f"ERROR: no route for agent '{agent}'")
        raise SystemExit(4)
    model_tier = agent_routes.resolve_model(agent, agents_yaml)
    binary, template = _resolve_tool_template(tool_name, agents_yaml)

    meta = _workflow_meta(state_raw, state_yaml_path)
    ticket_ctx = _fetch_ticket_context(ticket_id, repo_root)
    step_context = json.dumps(action.get("step_context") or {})
    prompt = build_prompt(action.get("instruction", ""), step_context, ticket_ctx, meta, ticket_id)
    overlay = overlay_text(repo_root, agent)
    if overlay:
        prompt = f"{prompt}\n{overlay}"

    prompt_file = tmp_dir / f"prompt_{step_id}.txt"
    prompt_file.write_text(prompt)

    work_dir = state_raw.get("worktree_path") or repo_root
    if not Path(work_dir).is_dir():
        work_dir = repo_root

    stdout_path = tmp_dir / f"out_{step_id}.txt"
    stderr_path = tmp_dir / f"err_{step_id}.txt"
    _log(f"  invoking {tool_name} ({binary})" + (f"  tier={model_tier}" if model_tier else ""))
    rc = invoke_tool(tool_name, binary, template, prompt, str(prompt_file),
                     model_tier, work_dir, stdout_path, stderr_path)
    if rc != 0:
        _log(f"WARN: tool '{binary}' exited {rc}")
        return _failed_payload(action, rc, _now_ms() - start_ms)

    adapter_tool = "cursor-agent" if tool_name == "cursor" else tool_name
    norm = split_stdout(adapter_tool, stdout_path, route_model=model_tier or None)
    usage = {k: v for k, v in norm.items() if k != "assistant_text"}
    try:
        completion = parse_completion(norm.get("assistant_text") or "")
    except ValueError as exc:
        # LOCKED policy: malformed COMPLETION is recoverable, not fatal.
        _log(f"WARN: malformed COMPLETION for {step_id} — recording failed (retryable): {exc}")
        return _failed_payload(action, 5, _now_ms() - start_ms)

    return _agent_payload(action, completion, usage, started_at, _now_ms() - start_ms)


# ---------------------------------------------------------------------------
# Script step execution — canonical path (lifted from bin/orchestrator inline
# arm; dead exit-10 soft-fail intentionally NOT carried).
# ---------------------------------------------------------------------------
def run_script_step(action: dict, *, state_yaml_path: str) -> tuple[bool, str]:
    """Run an inline script step. Returns (ok, new_state_path).

    ok=False  → script exited nonzero AND step has no on_failure routing: the
    workflow must abort (deterministic scripts like merge-to-main / create-worktree
    cannot self-heal by re-dispatch). Matches the old CLI inline arm's exit(3).
    ok=False is returned ONLY when re-dispatch would loop; if the contract has
    on_failure, the failure is recorded and the loop retries (ok=True).

    For archive-completed-change: durable pre-write BEFORE running (state file
    moves), so the entry survives the relocation; returns (True, relocated_path).
    """
    from orchestrator_next.parser import load_contract_for_step
    from orchestrator_next.paths import config_root
    from orchestrator_next.step_env import inline_script_env
    from orchestrator_next.step_runner import apply_step_paths, build_step_command
    from orchestrator_next.operator_workflow import load_step_params

    step_id = action["step_id"]
    phase = action.get("phase", "main")
    attempt = action.get("attempt", 1)
    state = load_state(state_yaml_path)
    contract = load_contract_for_step(step_id, state_yaml_path)
    env = inline_script_env(state, state_yaml_path, action_env=action.get("env", {}))
    croot = str(config_root())
    env = apply_step_paths(env, step_id=step_id, contract=contract, config_root=croot)
    for k, v in load_step_params(step_id).items():
        env.setdefault(k, v)
    run_cmd = build_step_command(step_id, contract, croot)

    _log(f"→ {step_id}  phase={phase}  kind=inline script  attempt={attempt}")
    _log(f"  run: {' '.join(run_cmd)}")

    state_mutating = step_id in _STATE_MUTATING_INLINE_STEPS
    if state_mutating:
        record(state_yaml_path, {
            "step_id": step_id, "phase": phase, "attempt": attempt,
            "status": "completed", "outputs": {},
            "evidence": {"summary": "recorded pre-script (state-mutating inline step)"},
        })

    cwd = env.get("REPO_ROOT") or None
    if cwd and not os.path.isdir(cwd):
        cwd = None
    proc = subprocess.run(run_cmd, capture_output=True, env=env, cwd=cwd)
    if proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
        sys.stderr.buffer.flush()

    new_state_path = state_yaml_path
    if proc.returncode != 0:
        if not state_mutating:
            record(state_yaml_path, {
                "step_id": step_id, "phase": phase, "attempt": attempt,
                "status": "failed", "outputs": {},
                "evidence": {"summary": f"script exited {proc.returncode}"},
            })
        _log(f"✗ {step_id}  failed  script_exit={proc.returncode}")
        # No script step carries on_failure routing (every on_failure source/
        # target in the schemas is an agent step). A failed deterministic script
        # can't self-heal via re-dispatch, so abort — matches the old CLI inline
        # arm's sys.exit(3). ok=False signals the loop to stop.
        return False, new_state_path

    if not state_mutating:
        outputs = {}
        lines = proc.stdout.decode(errors="replace").strip().splitlines()
        if lines:
            try:
                outputs = json.loads(lines[-1])
            except (json.JSONDecodeError, ValueError):
                pass
        payload = {
            "step_id": step_id, "phase": phase, "attempt": attempt,
            "status": "completed", "outputs": outputs,
            "evidence": {"outputs": outputs, "summary": "inline script completed"},
        }
        if isinstance(outputs.get("state_patch"), dict):
            payload["state_patch"] = outputs["state_patch"]
        record(state_yaml_path, payload)
        # archive relocation: re-point state path if it moved.
        new_state_path = _relocate_after_archive(step_id, outputs, state_yaml_path, new_state_path)
    else:
        # state-mutating script (archive) already recorded; relocate via stdout.
        outputs = {}
        lines = proc.stdout.decode(errors="replace").strip().splitlines()
        if lines:
            try:
                outputs = json.loads(lines[-1])
            except (json.JSONDecodeError, ValueError):
                pass
        new_state_path = _relocate_after_archive(step_id, outputs, state_yaml_path, new_state_path)

    _log(f"✓ {step_id}  done  status=completed")
    return True, new_state_path


def _relocate_after_archive(step_id, outputs, state_yaml_path, default) -> str:
    if step_id != "archive-completed-change":
        return default
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
# Per-step lifecycle hooks (contract pre: / post:)
# ---------------------------------------------------------------------------
def _run_hooks(
    hooks: list[str], kind: str, action: dict,
    state_yaml_path: str, repo_root: str, state_raw: dict,
) -> bool:
    """Run a step's pre/post hook commands. Returns True if all succeeded.

    Each hook runs via `bash -c` with the state path and step id in the env
    (ORCHESTRATOR_STATE_YAML, ORCHESTRATOR_STEP_ID) plus REPO_ROOT, cwd=repo.
    A pre-hook caller treats False as a block; a post-hook caller ignores it.
    """
    if not hooks:
        return True
    env = os.environ.copy()
    env["REPO_ROOT"] = repo_root
    env["ORCHESTRATOR_STATE_YAML"] = state_yaml_path
    env["ORCHESTRATOR_STEP_ID"] = action.get("step_id", "")
    cwd = state_raw.get("worktree_path") or repo_root
    if not Path(cwd).is_dir():
        cwd = repo_root
    for cmd in hooks:
        _log(f"  {kind}-hook: {cmd}")
        proc = subprocess.run(["bash", "-c", cmd], env=env, cwd=cwd,
                              capture_output=True, text=True)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        if proc.returncode != 0:
            _log(f"  {kind}-hook exited {proc.returncode}: {cmd}")
            return False
    return True


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def run_loop(state_yaml_path: str, ticket_id: str, *, repo_root: str, agents_yaml: str) -> int:
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # Operator rerun cleanup (spawn_failure_cap), as bash did on entry.
        try:
            from orchestrator_next.spawn_resume import apply_spawn_failure_resume
            apply_spawn_failure_resume(state_yaml_path)
        except Exception:
            pass

        while True:
            if not os.path.isfile(state_yaml_path):
                _log("Workflow complete (state archived).")
                return 1
            state = load_state(state_yaml_path)
            try:
                action, code = dispatch(state, state_yaml_path)
            except (ContractDispatchError, ParserContractDispatchError) as exc:
                _log(f"Contract error: {exc}")
                return 3
            if code == 1:
                _log("Workflow complete.")
                return 1
            if code == 2:
                _log("Workflow blocked.")
                return 2

            # pre hooks: run before the step body. A nonzero pre hook blocks
            # the workflow (exit 2) — the step's precondition isn't met.
            if not _run_hooks(action.get("pre") or [], "pre", action,
                              state_yaml_path, repo_root, state.raw):
                _log(f"Workflow blocked: pre-hook failed for {action['step_id']}.")
                return 2

            if action.get("agent"):
                _log(f"→ {action['step_id']}  phase={action.get('phase','main')}  "
                     f"kind=agent  agent={action['agent']}  attempt={action.get('attempt',1)}")
                payload = run_agent_step(
                    action, repo_root=repo_root, agents_yaml=agents_yaml,
                    ticket_id=ticket_id, state_raw=state.raw,
                    state_yaml_path=state_yaml_path, tmp_dir=tmp_dir,
                )
                result, rc = record(state_yaml_path, payload)
                if rc == 3:
                    # bad payload shape → record as failed (retryable), not fatal.
                    _log(f"WARN: record rejected payload for {action['step_id']} — recording failed")
                    record(state_yaml_path, _failed_payload(action, 3, payload.get("duration_ms", 0)))
                else:
                    _log(f"✓ {action['step_id']}  done  status={payload.get('status','completed')}")
            elif action.get("run"):
                ok, state_yaml_path = run_script_step(action, state_yaml_path=state_yaml_path)
                if not ok:
                    _log("Workflow aborted: deterministic script step failed.")
                    return 3
            else:
                _log("dispatch returned no actionable step; continuing")
                continue

            # post hooks: run after a successful step body. A nonzero post hook
            # is logged but never fails the step (teardown is best-effort).
            _run_hooks(action.get("post") or [], "post", action,
                       state_yaml_path, repo_root, state.raw)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# `orchestrator run` entry — arg parse + seeding + loop (replaces both shells)
# ---------------------------------------------------------------------------
_AGENT_ROUTE_RE = re.compile(r"^agent\.[a-zA-Z0-9_-]+\.(subprocess|model)=")


def _build_route_overrides(flags: list[str]) -> str:
    data: dict[str, dict[str, str]] = {}
    pat = re.compile(r"agent\.([a-zA-Z0-9_-]+)\.(subprocess|model)=(.+)")
    for flag in flags:
        m = pat.fullmatch(flag)
        if m:
            data.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    return json.dumps(data)


def _seed_state(slug: str, schema: str, repo_root: str, flag_overrides: list[str]) -> str:
    """Seed a state file via the existing Python helpers; return its path.
    Idempotent: reuse the newest *_<schema>_state.yaml if present."""
    orch_home = os.environ.get("ORCHESTRATOR_HOME", str(Path.home() / ".config" / "orchestrator"))
    schema_yaml = Path(orch_home) / "config" / "workflows" / f"{schema}.yaml"
    repo_override = Path(repo_root) / ".orchestrator" / "workflows" / f"{schema}.yaml"
    if repo_override.is_file():
        schema_yaml = repo_override
    if not schema_yaml.is_file():
        _log(f"ERROR: schema '{schema}' not found: {schema_yaml}")
        raise SystemExit(7)

    state_dir = Path(repo_root) / ".orchestrator" / slug
    existing = sorted(state_dir.glob(f"*_{schema}_state.yaml"))
    if existing:
        _log(f"state file exists at {existing[-1]} (idempotent skip)")
        return str(existing[-1])

    # scripts/lib is not a package (no __init__.py) — import by path.
    seed_parse_overrides = _import_by_path("seed_parse_overrides", "seed_parse_overrides.py")
    seed_write_state = _import_by_path("seed_write_state", "seed_write_state.py")

    # seed_parse_overrides.main prints INIT_JSON to stdout; capture it.
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = seed_parse_overrides.main([slug, schema, repo_root, str(schema_yaml), *flag_overrides])
    if rc != 0:
        raise SystemExit(1)
    init_json = buf.getvalue().strip()

    state_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    state_yaml = state_dir / f"{timestamp}_{schema}_state.yaml"
    prior = sorted(state_dir.glob("*_state.yaml"))
    prior_path = str(prior[-1]) if prior else ""

    if seed_write_state.main([str(state_yaml), init_json, prior_path]) != 0:
        state_yaml.unlink(missing_ok=True)
        raise SystemExit(1)

    from orchestrator_next import generate_plan as _gp
    try:
        _gp.generate_plan(str(state_yaml))
    except Exception as exc:
        state_yaml.unlink(missing_ok=True)
        _log(f"error: generate_plan failed: {exc}")
        raise SystemExit(2)

    _log(f"init-workflow: {slug} ({schema}) ready at {state_yaml}")
    return str(state_yaml)


def run_cmd(argv: list[str]) -> int:
    """`orchestrator run <ticket> [--schema S] [--repo P] [flag=value ...]`."""
    ticket_id = ""
    schema = "feature"
    repo_arg = ""
    flag_overrides: list[str] = []
    agent_route_flags: list[str] = []
    agents_config_arg = ""
    routes_override_arg = ""

    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--schema":
            schema = args.pop(0)
        elif a == "--repo":
            repo_arg = args.pop(0)
        elif a == "--agents-config":
            agents_config_arg = args.pop(0)
        elif a == "--routes-override":
            routes_override_arg = args.pop(0)
        elif a in ("--help", "-h"):
            _log("Usage: orchestrator run <ticket-id> [--schema S] [--repo PATH] [flag=value ...]")
            return 7
        elif a.startswith("-"):
            _log(f"ERROR: unknown option: {a}")
            return 7
        elif not ticket_id:
            ticket_id = a
        elif "=" in a:
            if _AGENT_ROUTE_RE.match(a):
                agent_route_flags.append(a)
            elif a.startswith("agents.config="):
                agents_config_arg = a[len("agents.config="):]
            else:
                flag_overrides.append(a)
        else:
            _log(f"ERROR: unexpected argument: {a}")
            return 7
    if not ticket_id:
        _log("Usage: orchestrator run <ticket-id> [--schema S] ...")
        return 7

    repo_root = os.path.abspath(repo_arg) if repo_arg else os.environ.get("REPO_ROOT", "")
    if not repo_root or not (Path(repo_root) / "spec" / "project.yaml").is_file():
        try:
            repo_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
            ).stdout.strip() or os.getcwd()
        except Exception:
            repo_root = os.getcwd()
    if not (Path(repo_root) / "spec" / "project.yaml").is_file():
        _log(f"ERROR: spec/project.yaml not found under {repo_root}")
        return 7
    os.environ["REPO_ROOT"] = repo_root

    # Route-override env (agent_routes.py reads these from os.environ).
    if routes_override_arg:
        os.environ["ORCHESTRATOR_ROUTES_YAML"] = os.path.abspath(routes_override_arg)
    if agents_config_arg:
        os.environ["ORCHESTRATOR_AGENTS_CONFIG"] = os.path.abspath(agents_config_arg)
    if agent_route_flags:
        os.environ["ORCHESTRATOR_AGENT_ROUTE_OVERRIDES"] = _build_route_overrides(agent_route_flags)

    slug = ticket_id.lower()
    state_yaml_path = _seed_state(slug, schema, repo_root, flag_overrides)

    # agents.yaml resolution (override > repo > global), mirrors run-workflow.sh.
    agents_yaml = os.environ.get("ORCHESTRATOR_AGENTS_CONFIG", "")
    if not agents_yaml:
        for cand in (
            Path(repo_root) / ".orchestrator" / "config" / "agents.yaml",
            Path(repo_root) / "config" / "agents.yaml",
            Path(os.environ.get("ORCHESTRATOR_HOME", "")) / "config" / "agents.yaml",
        ):
            if cand.is_file():
                agents_yaml = str(cand)
                break

    _log(f"Running workflow: ticket={ticket_id} schema={schema} state={state_yaml_path}")
    return run_loop(state_yaml_path, ticket_id, repo_root=repo_root, agents_yaml=agents_yaml)


if __name__ == "__main__":
    sys.exit(run_cmd(sys.argv[1:]))
