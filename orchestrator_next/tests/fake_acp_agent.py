#!/usr/bin/env python3
"""A fake ACP agent, for testing the ACP transport without a provider or API key.

Run as a subprocess by `test_acp_client.py`. Behaviour is driven entirely by
env vars so one binary can play every case the real agents produce:

  FAKE_ACP_TEXT          text to emit as agent_message_chunks (default: a COMPLETION block)
  FAKE_ACP_USAGE         "1" to report token usage on the PromptResponse
  FAKE_ACP_ASK           "1" to fire a session/request_permission before finishing
  FAKE_ACP_PLAN          "1" to emit a plan update
  FAKE_ACP_CRASH         "1" to exit non-zero during the turn
  FAKE_ACP_HANG          seconds to sleep inside the turn (for timeout tests)
  FAKE_ACP_MODEL         model id echoed back; set_config_option records what it got
  FAKE_ACP_REJECT_CONFIG "1" to raise on set_config_option (agents without model config)
"""
from __future__ import annotations

import asyncio
import os
import sys

import acp
import acp.schema as S
from acp.agent.connection import AgentSideConnection
from acp.stdio import stdio_streams

DEFAULT_TEXT = """Work complete.

COMPLETION:
  status: completed
  outputs:
    reason: "fake agent finished"
"""


class FakeAgent:
    def __init__(self, conn: AgentSideConnection) -> None:
        self.conn = conn
        self.config_seen: dict[str, str] = {}

    async def initialize(self, protocol_version: int, **kwargs) -> S.InitializeResponse:
        del kwargs
        return S.InitializeResponse(
            protocolVersion=min(protocol_version, acp.PROTOCOL_VERSION),
            agentCapabilities=S.AgentCapabilities(),
            agentInfo=S.Implementation(name="fake-acp-agent", version="1.0.0"),
        )

    async def new_session(self, cwd: str, **kwargs) -> S.NewSessionResponse:
        del cwd, kwargs
        return S.NewSessionResponse(sessionId="fake-session-1")

    async def set_config_option(self, config_id: str, session_id: str, value, **kwargs):
        del session_id, kwargs
        if os.environ.get("FAKE_ACP_REJECT_CONFIG") == "1":
            raise ValueError(f"unsupported config option {config_id!r}")
        self.config_seen[config_id] = str(value)
        return S.SetSessionConfigOptionResponse(configOptions=[])

    async def prompt(self, session_id: str, prompt, **kwargs) -> S.PromptResponse:
        del prompt, kwargs

        if os.environ.get("FAKE_ACP_CRASH") == "1":
            sys.stderr.write("fake agent: exploding on purpose\n")
            sys.stderr.flush()
            os._exit(3)

        hang = float(os.environ.get("FAKE_ACP_HANG") or 0)
        if hang:
            await asyncio.sleep(hang)

        if os.environ.get("FAKE_ACP_PLAN") == "1":
            await self.conn.session_update(
                session_id=session_id,
                update=S.AgentPlanUpdate(
                    sessionUpdate="plan",
                    entries=[
                        S.PlanEntry(content="read the spec", priority="high", status="completed"),
                        S.PlanEntry(content="write the code", priority="high", status="in_progress"),
                    ],
                ),
            )

        if os.environ.get("FAKE_ACP_ASK") == "1":
            result = await self.conn.request_permission(
                session_id=session_id,
                tool_call=S.ToolCallUpdate(toolCallId="tc-1", title="rm -rf build/"),
                options=[
                    S.PermissionOption(optionId="y", name="Allow", kind="allow_once"),
                    S.PermissionOption(optionId="Y", name="Always", kind="allow_always"),
                    S.PermissionOption(optionId="n", name="Reject", kind="reject_once"),
                ],
            )
            outcome = getattr(result.outcome, "outcome", "")
            picked = getattr(result.outcome, "option_id", "")
            await self.conn.session_update(
                session_id=session_id,
                update=S.AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=S.TextContentBlock(type="text", text=f"[permission {outcome}:{picked}]\n"),
                ),
            )

        text = os.environ.get("FAKE_ACP_TEXT")
        if text is None:
            text = DEFAULT_TEXT
        # Emit in chunks, the way a streaming agent does, so the client's
        # accumulation is genuinely exercised rather than a single-shot copy.
        for i in range(0, len(text), 24):
            await self.conn.session_update(
                session_id=session_id,
                update=S.AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=S.TextContentBlock(type="text", text=text[i : i + 24]),
                ),
            )

        usage = None
        if os.environ.get("FAKE_ACP_USAGE") == "1":
            usage = S.Usage(
                totalTokens=1500,
                inputTokens=1000,
                outputTokens=250,
                cachedReadTokens=200,
                cachedWriteTokens=50,
            )
        return S.PromptResponse(stopReason="end_turn", usage=usage)

    async def close_session(self, session_id: str, **kwargs):
        del session_id, kwargs
        return S.CloseSessionResponse()

    async def cancel(self, session_id: str, **kwargs) -> None:
        del session_id, kwargs

    async def authenticate(self, method_id: str, **kwargs):
        del method_id, kwargs
        return S.AuthenticateResponse()

    async def ext_method(self, method: str, params):
        del method, params
        return {}

    async def ext_notification(self, method: str, params) -> None:
        del method, params


async def main() -> None:
    reader, writer = await stdio_streams()
    # listening=False, then listen(): Connection.__init__ starts its own receive
    # loop by default, and calling listen() on top of that runs a SECOND one --
    # asyncio then raises "readuntil() called while another coroutine is already
    # waiting for incoming data" and the connection dies mid-turn.
    conn = AgentSideConnection(lambda c: FakeAgent(c), writer, reader, listening=False)
    await conn.listen()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
