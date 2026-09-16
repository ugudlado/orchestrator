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
"""
from __future__ import annotations

import json
import os
import re
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
def install_agent_runner(client: Any) -> None:
    """Point ``run_loop.AGENT_RUNNER`` at this client for the current process.

    Only headless mode does this. The engine's default stays
    ``NoAgentRunnerError`` so a harness-driven run can never silently start
    calling a model (protocol-v2 principle 1).
    """
    from orchestrator_next import run_loop

    def _runner(payload: dict[str, Any]) -> dict[str, Any]:
        outcome = run_judgment(payload, client=client)
        return {"assistant_text": outcome["text"], **outcome["usage"]}

    run_loop.AGENT_RUNNER = _runner


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


def drive(
    run_ref: str, *, client: Any | None = None, auto_approve: bool = False
) -> int:
    """Walk step/done to completion. Returns a CLI exit code.

    0 complete or parked at a gate · 2 needs_you · 3 error.

    A gate stops an unattended run by design: exit 0 with the preview printed,
    so a cron job does not read "failed" when it is simply waiting on a person.
    ``auto_approve`` is for pipelines that have already decided the run may
    write; it approves each gate with the token the engine just issued.
    """
    client = client or build_client()
    install_agent_runner(client)
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
            outcome = run_judgment(payload, client=client)
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
def run_headless_cmd(argv: list[str]) -> int:
    """`orchestrator run --headless <recipe> <slug> [--inputs JSON]`."""
    args = [a for a in argv if a != "--json"]
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
             "[--inputs JSON] [--auto-approve]")
        return 3

    try:
        started, _ = start(positionals[0], positionals[1],
                           inputs=inputs, ticket_id=ticket_id)
    except ProtocolError as exc:
        _log(str(exc))
        return 3
    print(json.dumps({k: v for k, v in started.items() if k != "next"},
                     sort_keys=True))
    try:
        return drive(started["state"], auto_approve=auto_approve)
    except HeadlessError as exc:
        _log(str(exc))
        return 3


def resume_headless_cmd(argv: list[str]) -> int:
    """`orchestrator headless <run>` — resume an existing run in headless mode."""
    auto_approve = "--auto-approve" in argv
    positionals = [a for a in argv if not a.startswith("-")]
    if not positionals:
        _log("usage: orchestrator headless <run> [--auto-approve]")
        return 3
    try:
        return drive(resolve_run(positionals[0]), auto_approve=auto_approve)
    except (ProtocolError, HeadlessError) as exc:
        _log(str(exc))
        return 3
