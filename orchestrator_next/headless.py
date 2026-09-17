"""Headless driver: the engine calls the model API itself.

``orchestrator run --headless <recipe> <slug>`` (and ``orchestrator headless
<run>`` to resume) walks the same ``step`` / ``done`` protocol the harness
walks, but runs each judgment step against the Anthropic Messages API instead
of handing the payload out. This is the cron/CI surface — it replaces the
bash drive loop that ``DRIVE.md`` used to describe.

Everything else in the engine stays model-agnostic: this module is the only
place that imports a vendor SDK, it is an optional install (``pip install
'orchestrator[headless]'``), and a harness-driven run never reaches it.

Tool surface is deliberately capped at ``fs.read``, ``fs.write``, ``fs.list``,
and ``shell.run`` (which covers ``git.*``). Anything bigger belongs in an exec
step, not in this runner — see the plan's "headless tool runner scope creep"
risk.

Two backends run the judgment steps:

``anthropic``
    The Messages API through the vendor SDK, with the built-in tool runner
    above. Needs ``ANTHROPIC_API_KEY`` (or an auth token).
``claude-cli``
    ``claude -p`` — Claude Code's own non-interactive mode, which runs on the
    machine's logged-in account, so a headless run needs no API key at all.
    Claude Code brings its own tools, so the built-in runner is unused here.

``claude-cli`` is the default whenever no API credential is in the environment,
which is the common case on a workstation. ``--backend`` (or
``ORCHESTRATOR_HEADLESS_BACKEND``) pins one explicitly.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from orchestrator_next.protocol import (
    ProtocolError,
    approve,
    done,
    resolve_run,
    start,
    step,
)

# Per-tool-call wall clock. A step that needs longer than this from one shell
# command is doing exec-step work.
SHELL_TIMEOUT_S = 300
# Truncation ceiling for anything a tool feeds back into the conversation.
MAX_TOOL_OUTPUT = 40_000
DEFAULT_MAX_TURNS = 30
DEFAULT_MAX_TOKENS = 16_000


class HeadlessError(RuntimeError):
    """Headless mode could not run (missing SDK, missing credentials, API error)."""


def _log(msg: str) -> None:
    print(f"[headless] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# built-in tools
# ---------------------------------------------------------------------------
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "fs_read",
        "description": "Read a UTF-8 text file. Paths are relative to the step's cwd.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fs_write",
        "description": "Write a UTF-8 text file, creating parent directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fs_list",
        "description": "List the entries of a directory (one per line).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "shell_run",
        "description": (
            "Run a shell command in the step's cwd and return its exit code, "
            "stdout, and stderr. Use this for git, tests, and build tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
]


def _resolve_in_cwd(cwd: Path, raw: str) -> Path:
    """Resolve ``raw`` under ``cwd`` and refuse anything that escapes it.

    The runner is cwd-bound on purpose: a headless run is unattended, so a
    path traversal out of the worktree is a silent corruption of the host
    checkout rather than a visible mistake.
    """
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve()
    root = cwd.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes the step cwd: {raw}")
    return resolved


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + f"\n... [truncated at {MAX_TOOL_OUTPUT} chars]"


def run_tool(name: str, args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    """Execute one built-in tool. Returns ``(result_text, is_error)``."""
    try:
        if name == "fs_read":
            return _truncate(
                _resolve_in_cwd(cwd, args["path"]).read_text(encoding="utf-8")
            ), False
        if name == "fs_write":
            target = _resolve_in_cwd(cwd, args["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(args.get("content") or ""), encoding="utf-8")
            return f"wrote {target}", False
        if name == "fs_list":
            target = _resolve_in_cwd(cwd, args["path"])
            entries = sorted(
                p.name + ("/" if p.is_dir() else "") for p in target.iterdir()
            )
            return _truncate("\n".join(entries) or "(empty)"), False
        if name == "shell_run":
            proc = subprocess.run(
                str(args["command"]),
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=SHELL_TIMEOUT_S,
            )
            body = (
                f"exit_code: {proc.returncode}\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
            return _truncate(body), proc.returncode != 0
    except subprocess.TimeoutExpired:
        return f"command timed out after {SHELL_TIMEOUT_S}s", True
    except (OSError, ValueError, KeyError) as exc:
        return f"{type(exc).__name__}: {exc}", True
    return f"unknown tool: {name}", True


# ---------------------------------------------------------------------------
# final-JSON extraction
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def extract_final_json(text: str) -> dict[str, Any]:
    """Pull the step's final JSON object out of the assistant's last message.

    Prefers the last fenced ```json block; falls back to the last bare
    top-level object. Raises ValueError when neither parses — the caller turns
    that into a retryable failed step rather than aborting the run.
    """
    for block in reversed(_FENCE_RE.findall(text or "")):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    depth = 0
    start_idx = -1
    candidates: list[str] = []
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start_idx >= 0:
                candidates.append(text[start_idx:i + 1])
    for chunk in reversed(candidates):
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in the model's final message")


# ---------------------------------------------------------------------------
# backend: claude-cli (`claude -p`, the logged-in Claude Code account)
# ---------------------------------------------------------------------------
BACKEND_ANTHROPIC = "anthropic"
BACKEND_CLAUDE_CLI = "claude-cli"
BACKENDS = (BACKEND_ANTHROPIC, BACKEND_CLAUDE_CLI)

# Contract `tools:` capability -> Claude Code tool names. Mirrors
# pack_export.TOOL_MAP so a step's declared surface means the same thing
# whichever surface runs it. Unknown capabilities are dropped, never invented.
CLI_TOOL_MAP: dict[str, list[str]] = {
    "fs.read": ["Read"],
    "fs.write": ["Write", "Edit"],
    "fs.list": ["Glob", "Grep"],
    "shell.run": ["Bash"],
    "shell.test": ["Bash"],
    "git.read": ["Bash"],
    "git.write": ["Bash"],
}

# `--json-schema` spends a turn of its own emitting the structured result, and
# Claude Code counts its own wrap-up turn too, so the contract's max_turns is
# the budget for *work* and needs headroom before it becomes the CLI's cap.
CLI_TURN_HEADROOM = 3
# Floor for the subprocess wall clock; longer steps scale with their turns.
CLI_MIN_TIMEOUT_S = 600
CLI_TIMEOUT_S_PER_TURN = 60


def cli_allowed_tools(tools: list[str]) -> list[str]:
    """Map contract capabilities to the Claude Code tool names to allow."""
    names: list[str] = []
    for capability in tools or []:
        for mapped in CLI_TOOL_MAP.get(str(capability), []):
            if mapped not in names:
                names.append(mapped)
    return names


def cli_result_schema(
    out_paths: dict[str, str], out_schema: dict[str, dict]
) -> dict[str, Any]:
    """Build the ``--json-schema`` for a step's declared outputs.

    Artifact outs report the path they were written to; typed outs carry their
    declared type (``enum`` becomes a string enum). ``status`` and ``reason``
    are always allowed so a step can report failure through the same block the
    engine already understands.
    """
    properties: dict[str, Any] = {
        name: {"type": "string"} for name in sorted(out_paths or {})
    }
    for name, spec in sorted((out_schema or {}).items()):
        declared = str(spec.get("type") or "string")
        if declared == "enum":
            properties[name] = {"type": "string",
                                "enum": [str(v) for v in spec.get("values") or []]}
        elif declared in ("integer", "number", "boolean"):
            properties[name] = {"type": declared}
        else:
            properties[name] = {"type": "string"}
    properties["status"] = {"type": "string",
                            "enum": ["completed", "failed", "abandoned"]}
    properties["reason"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": ["reason"],
        "additionalProperties": False,
    }


def _cli_usage(data: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Normalise the CLI's ``usage``/``modelUsage`` into the engine's shape."""
    raw = data.get("usage") or {}
    model_usage = data.get("modelUsage") or {}
    # modelUsage keys are the *actual* ids the run billed (a dated snapshot,
    # e.g. claude-haiku-4-5-20251001), which is what pricing wants; the alias
    # we asked for is the fallback.
    #
    # It can hold several: Claude Code bills its own background sub-tasks to
    # haiku alongside the model doing the work, in no meaningful order. Taking
    # the first key charged a Fable step at haiku rates, so prefer the id we
    # routed (matching the dated snapshot of the alias) and only fall back to
    # an arbitrary key when the route is not represented at all.
    reported = ""
    if isinstance(model_usage, dict) and model_usage:
        routed = [k for k in model_usage
                  if str(k) == model_id or str(k).startswith(f"{model_id}-")]
        reported = routed[0] if routed else next(iter(model_usage))
    usage = {
        "model": str(reported or model_id),
        "input_tokens": int(raw.get("input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
        "cache_read_input_tokens": int(raw.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(
            raw.get("cache_creation_input_tokens") or 0),
    }
    cost = data.get("total_cost_usd")
    if isinstance(cost, (int, float)):
        usage["cost_usd_reported"] = float(cost)
    return usage


def check_claude_cli() -> str:
    """Return the ``claude`` executable, or explain what is missing.

    Called once before any step runs: a headless run that discovers a missing
    CLI three judgment steps in has already spent real time and money.
    """
    found = shutil.which(os.environ.get("ORCHESTRATOR_CLAUDE_BIN") or "claude")
    if not found:
        raise HeadlessError(
            "the claude-cli backend needs the `claude` CLI on PATH — install "
            "Claude Code, or use --backend anthropic with ANTHROPIC_API_KEY set."
        )
    return found


def build_cli_argv(payload: dict[str, Any], *, executable: str = "claude") -> list[str]:
    """The exact ``claude -p`` argv for one judgment payload."""
    model_id = payload.get("model_id") or ""
    turns = int(payload.get("max_turns") or DEFAULT_MAX_TURNS) + CLI_TURN_HEADROOM
    argv = [
        executable, "-p",
        "--output-format", "json",
        "--model", model_id,
        "--max-turns", str(turns),
        "--permission-mode", "acceptEdits",
        "--no-session-persistence",
        "--system-prompt", str(payload.get("system") or ""),
        "--json-schema", json.dumps(
            cli_result_schema(payload.get("out") or {},
                              payload.get("out_schema") or {}),
            sort_keys=True),
    ]
    allowed = cli_allowed_tools(list(payload.get("tools") or []))
    if allowed:
        argv += ["--allowedTools", ",".join(allowed)]
    budget = os.environ.get("ORCHESTRATOR_STEP_BUDGET_USD")
    if budget:
        argv += ["--max-budget-usd", str(budget)]
    return argv


def _cli_login_hint(stderr: str) -> str | None:
    """Turn a not-logged-in stderr into the one action that fixes it."""
    lowered = (stderr or "").lower()
    if "not logged in" in lowered or "please log in" in lowered or (
        "login" in lowered and "run" in lowered
    ):
        return (
            "the claude-cli backend is not logged in — run `claude` once "
            "interactively to sign in, then re-run headless."
        )
    return None


def run_judgment_cli(
    payload: dict[str, Any], *, executable: str | None = None
) -> dict[str, Any]:
    """Run one judgment step through ``claude -p``.

    Returns the same ``{"out", "usage", "text"}`` shape the SDK backend does,
    so the drive loop does not care which backend produced it.
    """
    model_id = payload.get("model_id") or ""
    if not model_id:
        raise HeadlessError(
            f"step {payload.get('step_id')}: alias {payload.get('model')!r} "
            "resolved to no model id — check models.yaml"
        )
    cwd = Path(payload.get("cwd") or os.getcwd())
    argv = build_cli_argv(payload, executable=executable or check_claude_cli())
    turns = int(payload.get("max_turns") or DEFAULT_MAX_TURNS) + CLI_TURN_HEADROOM
    timeout = max(CLI_MIN_TIMEOUT_S, turns * CLI_TIMEOUT_S_PER_TURN)

    try:
        proc = subprocess.run(
            argv,
            input=(
                "Begin this step now. Use your tools to read and write files "
                f"under {cwd}. Report the declared outputs as the final JSON."
            ),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HeadlessError(
            f"step {payload.get('step_id')}: claude -p timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise HeadlessError(
            f"step {payload.get('step_id')}: could not run {argv[0]}: {exc}"
        ) from exc

    stderr_tail = (proc.stderr or "").strip()[-2000:]
    hint = _cli_login_hint(stderr_tail)
    if hint:
        raise HeadlessError(hint)

    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise HeadlessError(
            f"step {payload.get('step_id')}: claude -p exited "
            f"{proc.returncode} with unparseable output: "
            f"{stderr_tail or (proc.stdout or '')[-2000:]}"
        ) from exc
    if not isinstance(data, dict):
        raise HeadlessError(
            f"step {payload.get('step_id')}: claude -p returned "
            f"{type(data).__name__}, expected a JSON object"
        )

    text = str(data.get("result") or "")
    usage = _cli_usage(data, model_id)

    if proc.returncode != 0 or data.get("is_error"):
        detail = "; ".join(str(e) for e in (data.get("errors") or [])) or (
            data.get("subtype") or stderr_tail or f"exit {proc.returncode}")
        raise HeadlessError(
            f"step {payload.get('step_id')}: claude -p failed: {detail}"
        )

    structured = data.get("structured_output")
    if isinstance(structured, dict):
        return {"out": structured, "usage": usage, "text": text}
    try:
        return {"out": extract_final_json(text), "usage": usage, "text": text}
    except ValueError as exc:
        raise HeadlessError(f"step {payload.get('step_id')}: {exc}") from exc


# ---------------------------------------------------------------------------
# backend selection
# ---------------------------------------------------------------------------
def has_api_credentials() -> bool:
    """True when the environment already carries an Anthropic API credential."""
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def resolve_backend(requested: str | None = None) -> str:
    """Pick the backend: explicit flag, then env, then credentials.

    The default is ``claude-cli`` unless an API credential is present, because
    a workstation with Claude Code logged in and no key is the normal case —
    headless should just work there rather than demand a key.
    """
    choice = (requested or os.environ.get("ORCHESTRATOR_HEADLESS_BACKEND")
              or "").strip()
    if choice:
        if choice not in BACKENDS:
            raise HeadlessError(
                f"unknown headless backend {choice!r} — "
                f"expected one of {', '.join(BACKENDS)}"
            )
        return choice
    return BACKEND_ANTHROPIC if has_api_credentials() else BACKEND_CLAUDE_CLI


# ---------------------------------------------------------------------------
# the model call
# ---------------------------------------------------------------------------
def build_client() -> Any:
    """Construct an Anthropic client, or explain what is missing.

    Credentials resolve through the SDK's own chain (ANTHROPIC_API_KEY, an
    auth token, or an `ant auth login` profile) — the engine does not
    second-guess it.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover — depends on install extras
        raise HeadlessError(
            "headless mode needs the Anthropic SDK: "
            "pip install 'orchestrator[headless]'"
        ) from exc
    client = anthropic.Anthropic()
    # The SDK resolves credentials lazily, on the first request — which, in
    # this driver, happens several judgment turns into a run (after any
    # earlier script steps already did their work). Check the same chain it
    # would use up front so a missing key fails before anything runs, with a
    # clear message instead of a bare TypeError deep in the tool-use loop.
    if not client.api_key and not getattr(client, "auth_token", None):
        raise HeadlessError(
            "headless mode needs Anthropic credentials: set ANTHROPIC_API_KEY "
            "(or ANTHROPIC_AUTH_TOKEN, or run `ant auth login`) before "
            "`orchestrator run --headless`."
        )
    return client


def run_judgment(
    payload: dict[str, Any], *, client: Any, max_turns: int | None = None
) -> dict[str, Any]:
    """Run one judgment step to its final JSON block.

    Returns ``{"out": {...}, "usage": {...}, "text": str}``. Raises
    ``HeadlessError`` when the model never produced a parseable final block
    within ``max_turns``.
    """
    model_id = payload.get("model_id") or ""
    if not model_id:
        raise HeadlessError(
            f"step {payload.get('step_id')}: alias {payload.get('model')!r} "
            "resolved to no model id — check models.yaml"
        )
    cwd = Path(payload.get("cwd") or os.getcwd())
    turns = max_turns or payload.get("max_turns") or DEFAULT_MAX_TURNS

    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": (
            "Begin this step now. Use the tools to read and write files under "
            f"{cwd}. End with the final JSON block described above."
        ),
    }]
    usage_total = {
        "model": model_id,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }

    for turn in range(int(turns)):
        response = client.messages.create(
            model=model_id,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=payload.get("system") or "",
            messages=messages,
            tools=TOOL_DEFS,
        )
        usage = getattr(response, "usage", None)
        for key in usage_total:
            if key == "model":
                continue
            value = getattr(usage, key, 0) or 0
            usage_total[key] += int(value)

        blocks = list(getattr(response, "content", None) or [])
        text = "\n".join(
            str(getattr(b, "text", "")) for b in blocks
            if getattr(b, "type", "") == "text"
        )
        tool_uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]

        messages.append({
            "role": "assistant",
            "content": [
                b.model_dump() if hasattr(b, "model_dump") else b for b in blocks
            ],
        })

        if not tool_uses:
            try:
                return {"out": extract_final_json(text), "usage": usage_total,
                        "text": text}
            except ValueError as exc:
                if turn == int(turns) - 1:
                    raise HeadlessError(
                        f"step {payload.get('step_id')}: {exc}"
                    ) from exc
                messages.append({
                    "role": "user",
                    "content": (
                        "That message had no parseable final JSON block. "
                        "Reply with only the ```json block described above."
                    ),
                })
                continue

        results = []
        for block in tool_uses:
            args = getattr(block, "input", None) or {}
            out_text, is_error = run_tool(str(block.name), dict(args), cwd)
            _log(f"  tool {block.name} -> {'error' if is_error else 'ok'}")
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": out_text,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})

    raise HeadlessError(
        f"step {payload.get('step_id')}: exhausted {turns} turns without a "
        "final JSON block"
    )


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------
def _print_gate_preview(result: dict[str, Any]) -> None:
    """Show what a human would need to approve this gate, then the command."""
    payload = result.get("payload") or {}
    preview = payload.get("preview") or {}
    token = payload.get("token") or ""
    _log(f"gate {result.get('step_id')} — approve to continue")
    for name, entry in (preview.get("show") or {}).items():
        state = "missing" if not entry.get("exists") else entry.get("sha256", "")[:12]
        _log(f"  show.{name}: {entry.get('path')} [{state}]")
    print(json.dumps({"status": "blocked", "kind": "gate",
                      "step_id": result.get("step_id"),
                      "payload": payload}, sort_keys=True, indent=2, default=str))
    _log(f"resume with: orchestrator approve <run> {token}")


def build_step_runner(
    backend: str | None = None, *, client: Any | None = None
) -> Any:
    """Return the per-step callable for ``backend``, failing fast if unusable.

    Both arms do their "can this run at all" check here, before the loop takes
    its first step, so a missing key or a missing CLI is a clear message rather
    than a crash several steps into an unattended run.
    """
    if client is not None:
        return lambda payload: run_judgment(payload, client=client)
    choice = resolve_backend(backend)
    if choice == BACKEND_CLAUDE_CLI:
        executable = check_claude_cli()
        _log(f"backend: claude-cli ({executable})")
        return lambda payload: run_judgment_cli(payload, executable=executable)
    built = build_client()
    _log("backend: anthropic (Messages API)")
    return lambda payload: run_judgment(payload, client=built)


def drive(
    run_ref: str,
    *,
    client: Any | None = None,
    auto_approve: bool = False,
    backend: str | None = None,
) -> int:
    """Walk step/done to completion. Returns a CLI exit code.

    0 complete or parked at a gate · 2 needs_you · 3 error.

    A gate stops an unattended run by design: exit 0 with the preview printed,
    so a cron job does not read "failed" when it is simply waiting on a person.
    ``auto_approve`` is for pipelines that have already decided the run may
    write; it approves each gate with the token the engine just issued.
    """
    run_step = build_step_runner(backend, client=client)
    while True:
        result, _ = step(run_ref)
        status_value = result.get("status")

        if status_value == "done":
            _log("run complete")
            return 0
        if status_value == "blocked" and result.get("kind") == "gate":
            token = (result.get("payload") or {}).get("token") or ""
            if not auto_approve:
                _print_gate_preview(result)
                return 0
            _log(f"auto-approving gate {result.get('step_id')}")
            try:
                approve(run_ref, token, edits={"auto_approved": True})
            except ProtocolError as exc:
                _log(f"auto-approve rejected: {exc}")
                return 3
            continue
        if status_value in ("blocked", "needs_you"):
            _log(f"{status_value}: {result.get('detail') or result.get('kind')}")
            return 2
        if status_value == "error":
            _log(f"error: {result.get('detail')}")
            return 3
        if status_value != "ready" or result.get("kind") != "judgment":
            _log(f"unexpected step result: {json.dumps(result, default=str)}")
            return 3

        payload = result["payload"]
        step_id = result["step_id"]
        _log(f"-> {step_id}  model={payload.get('model_id')}")
        try:
            outcome = run_step(payload)
        except HeadlessError as exc:
            _log(f"judgment failed: {exc}")
            return 3

        out = dict(outcome["out"])
        step_status = "completed"
        if str(out.pop("status", "completed")).lower() in ("failed", "abandoned"):
            step_status = "abandoned"
        try:
            done(run_ref, step_id, out=out, usage=outcome["usage"],
                 status=step_status)
        except ProtocolError as exc:
            _log(f"done rejected: {exc}")
            return 3
        _log(f"<- {step_id}  {step_status}")


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------
def _take_backend(args: list[str]) -> str | None:
    """Pop ``--backend <name>`` out of ``args`` in place, returning its value.

    Parsed here rather than in ``cli.py``: headless owns its own flags so the
    top-level parser stays backend-agnostic.
    """
    if "--backend" not in args:
        return None
    i = args.index("--backend")
    value = args[i + 1] if i + 1 < len(args) else ""
    del args[i:i + 2]
    return value or None


def run_headless_cmd(argv: list[str]) -> int:
    """`orchestrator run --headless <recipe> <slug> [--inputs JSON]`."""
    args = [a for a in argv if a != "--json"]
    backend = _take_backend(args)
    inputs = None
    if "--inputs" in args:
        i = args.index("--inputs")
        try:
            inputs = json.loads(args[i + 1])
        except (IndexError, json.JSONDecodeError) as exc:
            _log(f"--inputs must be valid JSON — {exc}")
            return 3
        del args[i:i + 2]
    ticket_id = ""
    if "--ticket-id" in args:
        i = args.index("--ticket-id")
        ticket_id = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    auto_approve = "--auto-approve" in args
    args = [a for a in args if a != "--auto-approve"]
    positionals = [a for a in args if not a.startswith("-")]
    if len(positionals) < 2:
        _log("usage: orchestrator run --headless <recipe> <slug> "
             "[--inputs JSON] [--auto-approve] "
             "[--backend anthropic|claude-cli]")
        return 3

    # Resolve the backend before `start` writes a run: an unknown backend name
    # should not leave a half-seeded run behind.
    try:
        resolve_backend(backend)
    except HeadlessError as exc:
        _log(str(exc))
        return 3

    try:
        started, _ = start(positionals[0], positionals[1],
                           inputs=inputs, ticket_id=ticket_id)
    except ProtocolError as exc:
        _log(str(exc))
        return 3
    print(json.dumps({k: v for k, v in started.items() if k != "next"},
                     sort_keys=True))
    # `start` resumes a live slug instead of re-seeding, keeping the run's
    # original `user_input`. Driving on would judge the *previous* ticket text
    # under the new one's name, so refuse rather than spend a run on it.
    if started.get("resumed") and inputs:
        _log(f"{positionals[1]} is already a live run (resumed) — its inputs "
             "are fixed at seed time, so --inputs would be ignored. Resume it "
             f"with `orchestrator headless {positionals[1]}`, or start a new "
             "slug to use these inputs.")
        return 3
    try:
        return drive(started["state"], auto_approve=auto_approve,
                     backend=backend)
    except HeadlessError as exc:
        _log(str(exc))
        return 3


def resume_headless_cmd(argv: list[str]) -> int:
    """`orchestrator headless <run>` — resume an existing run in headless mode."""
    args = list(argv)
    backend = _take_backend(args)
    auto_approve = "--auto-approve" in args
    positionals = [a for a in args if not a.startswith("-")]
    if not positionals:
        _log("usage: orchestrator headless <run> [--auto-approve] "
             "[--backend anthropic|claude-cli]")
        return 3
    try:
        return drive(resolve_run(positionals[0]), auto_approve=auto_approve,
                     backend=backend)
    except (ProtocolError, HeadlessError) as exc:
        _log(str(exc))
        return 3
