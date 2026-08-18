"""ACP transport for agent steps — replaces per-CLI argv templates and stdout adapters.

Why this exists
---------------
Before ACP, every agent needed two hand-written pieces:

  * `tools.<name>.args_template` in models.yaml — the exact argv incantation
  * an adapter in `usage_adapters.py` — a bespoke parser for that CLI's JSON or
    JSONL dialect, one per tool, each with its own fixture set

That is O(N) work per agent and it breaks silently whenever an upstream CLI
changes its output. The Agent Client Protocol (JSON-RPC over stdio, Apache-2.0,
backed by Zed and JetBrains) replaces both with one negotiated interface.

This module speaks the CLIENT half. orchestrator spawns an ACP agent, runs one
prompt turn, and returns the SAME `NormalizedResult` shape `split_stdout()`
returns, so `run_agent_step` -> `parse_completion` -> `record` is untouched.

Verified agent launch commands (2026-08-18):

  claude   npx -y @zed-industries/claude-agent-acp     (bin claude-agent-acp, 0.23.1)
  codex    npx -y @zed-industries/codex-acp            (bin codex-acp, 0.16.0)
  cursor   cursor-agent acp                            (native subcommand, protocol v1)

Deliberate design decisions
---------------------------
1. **Autonomous permission policy.** orchestrator's agent steps are unattended;
   the pre-ACP argv templates passed `--force` / `--yolo` to get exactly this.
   `request_permission` therefore auto-selects an allow option by default. This
   is not a new grant of power — it is the same grant, now explicit and
   overridable via `ORCHESTRATOR_ACP_PERMISSION` (allow | allow_always | reject).

2. **No client filesystem or terminal capability advertised.** ACP lets a client
   offer `fs/*` and `terminal/*` back to the agent. orchestrator's agents already
   own the real working tree, exactly as they did when they were CLIs, so
   advertising nothing keeps the blast radius identical to today. (v2 removes
   these from the protocol anyway, deferring to MCP.)

3. **Usage is best-effort, and that is a real caveat.** `PromptResponse.usage`
   is marked UNSTABLE in the schema — "not part of the spec yet, and may be
   removed or changed at any point". We read it, fall back to `session_info_update`
   and `usage_update`, and return zeros when an agent reports nothing. Zeroed
   usage flows into `record._is_spawn_failure` the same way it always did, so
   set `ORCHESTRATOR_SKIP_USAGE_CHECK` when running an agent that does not report.

4. **The sync core stays sync.** Everything under `run_loop.py` is synchronous by
   design and that simplicity is load-bearing. The async boundary is exactly one
   `asyncio.run()` inside `run_turn()`.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Any

from orchestrator_next.usage_adapters import NormalizedResult, ZEROED_USAGE

DEFAULT_TIMEOUT_S = 3600
_ALLOW_KINDS = ("allow_always", "allow_once")
_REJECT_KINDS = ("reject_once", "reject_always")


class AcpUnavailableError(RuntimeError):
    """The acp SDK is not installed. Raised at call time, never at import time."""


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("orchestrator")
    except Exception:  # noqa: BLE001 - dev checkouts are not installed
        return "0.0.0"


def _log(message: str) -> None:
    print(f"acp: {message}", file=sys.stderr)


def _text_of(block: Any) -> str:
    """Pull plain text out of an ACP content block, ignoring non-text blocks."""
    if getattr(block, "type", None) == "text":
        return str(getattr(block, "text", "") or "")
    if isinstance(block, dict) and block.get("type") == "text":
        return str(block.get("text") or "")
    return ""


def _permission_policy() -> str:
    value = (os.environ.get("ORCHESTRATOR_ACP_PERMISSION") or "allow").strip().lower()
    return value if value in ("allow", "allow_always", "reject") else "allow"


def _pick_permission_option(options: list[Any], policy: str) -> Any | None:
    """Choose an option by kind, preferring the policy's kind order."""
    if not options:
        return None
    if policy == "reject":
        wanted = _REJECT_KINDS
    elif policy == "allow_always":
        wanted = ("allow_always", "allow_once")
    else:
        wanted = ("allow_once", "allow_always")
    for kind in wanted:
        for opt in options:
            if str(getattr(opt, "kind", "") or "") == kind:
                return opt
    # Unknown vocabulary: fall back to the first option rather than hanging the
    # turn. An agent that offers only custom kinds still makes progress.
    return options[0]


def _usage_to_orchestrator(usage: Any) -> dict[str, Any]:
    """ACP `Usage` -> orchestrator's four token classes.

    The mapping is 1:1, which is the single strongest argument for this
    transport: no dialect parsing, no per-tool fixtures.
    """
    if usage is None:
        return {}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(usage, "cached_read_tokens", 0) or 0),
        "cache_creation_input_tokens": int(getattr(usage, "cached_write_tokens", 0) or 0),
    }


def _build_client_class(sink: dict[str, Any]):
    """Build the orchestrator-side ACP Client, closing over a mutable sink.

    Imports the SDK lazily so `import orchestrator_next.acp_client` stays free
    of a hard pydantic dependency for anyone not using this transport.
    """
    import acp
    import acp.schema as acp_schema

    class OrchestratorClient:
        """Minimal ACP client: collect text, plan and usage; auto-resolve gates."""

        async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
            del session_id, kwargs
            kind = str(getattr(update, "session_update", "") or "")

            if kind == "agent_message_chunk":
                sink["text"].append(_text_of(getattr(update, "content", None)))

            elif kind == "agent_thought_chunk":
                sink["thoughts"].append(_text_of(getattr(update, "content", None)))

            elif kind in ("plan", "plan_update"):
                # The agent's plan. orchestrator has its own workflow_plan, so this
                # is recorded as evidence rather than driving anything.
                entries = getattr(update, "entries", None) or []
                sink["plan"] = [
                    {
                        "content": str(getattr(e, "content", "") or ""),
                        "status": str(getattr(e, "status", "") or ""),
                        "priority": str(getattr(e, "priority", "") or ""),
                    }
                    for e in entries
                ]

            elif kind in ("tool_call", "tool_call_update"):
                name = getattr(update, "title", None) or getattr(update, "tool_call_id", None)
                if name:
                    sink["tools"].append(str(name))

            elif kind == "session_info_update":
                usage = getattr(update, "usage", None)
                if usage is not None:
                    sink["usage"] = usage
                model = getattr(update, "model", None) or getattr(update, "model_id", None)
                if model:
                    sink["model"] = str(model)

            elif kind == "usage_update":
                # Context-window pressure, not per-turn tokens. Only `cost` is
                # useful to us, and only when the agent bothers to send it.
                cost = getattr(update, "cost", None)
                if cost is not None and getattr(cost, "amount", None) is not None:
                    sink["cost_usd"] = float(cost.amount)
                    sink["cost_currency"] = str(getattr(cost, "currency", "") or "")

        async def request_permission(
            self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any
        ) -> Any:
            del session_id, kwargs
            policy = _permission_policy()
            chosen = _pick_permission_option(list(options or []), policy)
            if chosen is None:
                sink["permissions"].append({"policy": policy, "outcome": "cancelled"})
                return acp.RequestPermissionResponse(
                    outcome=acp.RequestPermissionOutcomeCancelled(outcome="cancelled")
                )
            label = getattr(tool_call, "title", None) or getattr(tool_call, "tool_call_id", "?")
            sink["permissions"].append(
                {"tool": str(label), "policy": policy, "option": str(getattr(chosen, "kind", ""))}
            )
            return acp.RequestPermissionResponse(
                outcome=acp_schema.AllowedOutcome(outcome="selected", option_id=chosen.option_id)
            )

        # --- capabilities we deliberately do NOT advertise ------------------
        # The agent owns the working tree, exactly as it did as a CLI. Raising
        # here is louder than silently returning empty content, which would let
        # an agent believe a file was blank.
        async def write_text_file(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError("orchestrator does not advertise fs/write_text_file")

        async def read_text_file(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError("orchestrator does not advertise fs/read_text_file")

        async def create_terminal(self, *a: Any, **k: Any) -> Any:
            raise NotImplementedError("orchestrator does not advertise terminal/create")

        async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            del params
            _log(f"ignoring unsupported ext method {method!r}")
            return {}

        async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
            del method, params

    return OrchestratorClient


async def _run_turn_async(
    *,
    command: str,
    args: list[str],
    prompt: str,
    cwd: str | None,
    model_id: str,
    env: dict[str, str] | None,
    timeout_s: int,
) -> NormalizedResult:
    import acp
    import acp.schema as acp_schema
    from acp.stdio import spawn_agent_process

    sink: dict[str, Any] = {
        "text": [], "thoughts": [], "tools": [], "plan": [], "permissions": [],
        "usage": None, "model": None, "cost_usd": None, "cost_currency": None,
        "stop_reason": None,
    }
    client_cls = _build_client_class(sink)

    spawn_env = {**os.environ, **{k: str(v) for k, v in (env or {}).items() if v is not None}}

    async with spawn_agent_process(
        lambda _conn: client_cls(), command, *args, env=spawn_env, cwd=cwd
    ) as (conn, process):
        await conn.initialize(
            protocol_version=acp.PROTOCOL_VERSION,
            client_capabilities=acp_schema.ClientCapabilities(
                # Everything off: see module docstring decision 2.
                fs=acp_schema.FileSystemCapabilities(read_text_file=False, write_text_file=False),
                terminal=False,
            ),
            client_info=acp_schema.Implementation(name="orchestrator", version=_version()),
        )

        session = await conn.new_session(cwd=cwd or os.getcwd(), mcp_servers=[])
        session_id = session.session_id

        # Model selection is a per-agent config option in ACP, not a protocol
        # field. Best-effort: an agent that does not expose the option keeps its
        # own default, and we say so rather than pretending the route applied.
        if model_id:
            try:
                await conn.set_config_option(
                    config_id="model", session_id=session_id, value=model_id
                )
            except Exception as exc:  # noqa: BLE001 - optional capability
                _log(f"could not set model={model_id!r} via config option ({exc}); "
                     f"agent default applies")

        response = await asyncio.wait_for(
            conn.prompt(
                session_id=session_id,
                prompt=[acp_schema.TextContentBlock(type="text", text=prompt)],
            ),
            timeout=timeout_s,
        )
        sink["stop_reason"] = str(getattr(response, "stop_reason", "") or "")
        # PromptResponse.usage is UNSTABLE (see module docstring decision 3);
        # prefer it, fall back to whatever session_info_update carried.
        if getattr(response, "usage", None) is not None:
            sink["usage"] = response.usage

        try:
            await conn.close_session(session_id=session_id)
        except Exception:  # noqa: BLE001 - teardown must not fail a good turn
            pass

        if process.returncode not in (None, 0):
            _log(f"agent process exited {process.returncode} after the turn")

    result: NormalizedResult = {
        "assistant_text": "".join(sink["text"]),
        **ZEROED_USAGE,
        "model": sink["model"] or (model_id or None),
    }
    result.update(_usage_to_orchestrator(sink["usage"]))
    if sink["cost_usd"] is not None and (sink["cost_currency"] or "USD").upper() == "USD":
        # An agent-reported cost wins over pricing.py's table: it is the
        # authority on what its own vendor charged.
        result["cost_usd"] = sink["cost_usd"]

    result["acp"] = {
        "stop_reason": sink["stop_reason"],
        "tools": sink["tools"],
        "plan": sink["plan"],
        "permissions": sink["permissions"],
        "thoughts_chars": sum(len(t) for t in sink["thoughts"]),
    }
    return result


def run_turn(
    *,
    command: str,
    args: list[str] | None = None,
    prompt: str,
    cwd: str | None = None,
    model_id: str = "",
    env: dict[str, str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> NormalizedResult:
    """Run one ACP prompt turn and return orchestrator's NormalizedResult.

    Never raises for agent-side failure: a crashed or unparseable agent returns
    zeroed usage and empty text, which `run_agent_step` already treats as a
    retryable `failed` outcome via the spawn-failure cap. Only a missing SDK or
    a missing binary raises, because those are configuration errors the operator
    must see immediately rather than burn retries on.
    """
    try:
        import acp  # noqa: F401
    except ImportError as exc:
        raise AcpUnavailableError(
            "the 'agent-client-protocol' package is required for transport: acp — "
            "install it with `uv add agent-client-protocol` or `pip install agent-client-protocol`"
        ) from exc

    if not shutil.which(command):
        raise FileNotFoundError(
            f"acp: agent binary {command!r} not found on PATH. "
            f"Check tools.<name>.binary in models.yaml."
        )

    try:
        return asyncio.run(
            _run_turn_async(
                command=command,
                args=list(args or []),
                prompt=prompt,
                cwd=cwd,
                model_id=model_id,
                env=env,
                timeout_s=timeout_s,
            )
        )
    except asyncio.TimeoutError:
        _log(f"turn exceeded {timeout_s}s — recording a retryable failure")
        return {"assistant_text": "", **ZEROED_USAGE, "acp": {"stop_reason": "timeout"}}
    except Exception as exc:  # noqa: BLE001 - see docstring: agent faults are retryable
        _log(f"turn failed: {type(exc).__name__}: {exc}")
        return {
            "assistant_text": "",
            **ZEROED_USAGE,
            "acp": {"stop_reason": "error", "error": f"{type(exc).__name__}: {exc}"},
        }
