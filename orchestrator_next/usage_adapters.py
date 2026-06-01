"""Per-tool stdout usage adapters (ORC-111).

Normalize agent CLI stdout into assistant text plus a usage dict compatible
with record.py's payload vocabulary.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

NormalizedResult = dict[str, Any]
AdapterFn = Callable[[str, str | None], NormalizedResult]

_ZEROED_USAGE: dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "model": None,
    "cost_usd": None,
}


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _empty_result(*, assistant_text: str = "") -> NormalizedResult:
    return {"assistant_text": assistant_text, **_ZEROED_USAGE}


def _result(
    *,
    assistant_text: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
    model: str | None,
    cost_usd: float | None,
) -> NormalizedResult:
    return {
        "assistant_text": assistant_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "model": model,
        "cost_usd": cost_usd,
    }


def _read_text(stdout_path: os.PathLike[str] | str) -> str | None:
    path = Path(stdout_path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        _warn(f"usage_adapters: could not read stdout file: {path}")
        return None


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _parse_jsonl(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _adapt_claude(raw: str, route_model: str | None) -> NormalizedResult:
    del route_model
    data = _parse_json_object(raw)
    if data is None:
        raise ValueError("invalid claude JSON")

    usage = data.get("usage") or {}
    model_usage = data.get("modelUsage") or {}
    model = next(iter(model_usage), None) if model_usage else None

    return _result(
        assistant_text=str(data.get("result") or ""),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        model=model,
        cost_usd=data.get("total_cost_usd"),
    )


def _adapt_cursor_agent(raw: str, route_model: str | None) -> NormalizedResult:
    data = _parse_json_object(raw)
    if data is None:
        raise ValueError("invalid cursor-agent JSON")

    usage = data.get("usage") or {}
    model = data.get("model") or route_model

    return _result(
        assistant_text=str(data.get("result") or ""),
        input_tokens=int(usage.get("inputTokens") or 0),
        output_tokens=int(usage.get("outputTokens") or 0),
        cache_read_input_tokens=int(usage.get("cacheReadTokens") or 0),
        cache_creation_input_tokens=int(usage.get("cacheWriteTokens") or 0),
        model=model,
        cost_usd=None,
    )


def _extract_codex_assistant_text(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def _adapt_codex(raw: str, route_model: str | None) -> NormalizedResult:
    events = _parse_jsonl(raw)
    if not events:
        raise ValueError("invalid codex JSONL")

    usage_event: dict[str, Any] | None = None
    for event in events:
        if event.get("type") == "turn.completed":
            usage_event = event

    if usage_event is None:
        raise ValueError("codex JSONL missing turn.completed")

    usage = usage_event.get("usage") or {}
    return _result(
        assistant_text=_extract_codex_assistant_text(events),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_input_tokens=int(usage.get("cached_input_tokens") or 0),
        cache_creation_input_tokens=0,
        model=route_model,
        cost_usd=None,
    )


def _extract_omp_assistant_text(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("type") not in ("message_start", "turn_end"):
            continue
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
    return "".join(parts)


def _adapt_omp(raw: str, route_model: str | None) -> NormalizedResult:
    del route_model
    events = _parse_jsonl(raw)
    if not events:
        raise ValueError("invalid omp JSONL")

    turn_end: dict[str, Any] | None = None
    for event in events:
        if event.get("type") == "turn_end":
            turn_end = event

    if turn_end is None:
        raise ValueError("omp JSONL missing turn_end")

    message = turn_end.get("message") or {}
    usage = message.get("usage") or {}
    cost = usage.get("cost") or {}

    return _result(
        assistant_text=_extract_omp_assistant_text(events),
        input_tokens=int(usage.get("input") or 0),
        output_tokens=int(usage.get("output") or 0),
        cache_read_input_tokens=int(usage.get("cacheRead") or 0),
        cache_creation_input_tokens=int(usage.get("cacheWrite") or 0),
        model=message.get("model"),
        cost_usd=cost.get("total"),
    )


def _adapt_passthrough(raw: str, route_model: str | None) -> NormalizedResult:
    del route_model
    return _empty_result(assistant_text=raw)


TOOL_ADAPTERS: dict[str, AdapterFn] = {
    "claude": _adapt_claude,
    "cursor-agent": _adapt_cursor_agent,
    "codex": _adapt_codex,
    "omp": _adapt_omp,
}


def split_stdout(
    tool: str,
    stdout_path: os.PathLike[str] | str,
    *,
    route_model: str | None = None,
) -> NormalizedResult:
    """Split captured tool stdout into assistant text and normalized usage."""
    raw = _read_text(stdout_path)
    if raw is None:
        return _empty_result()

    adapter = TOOL_ADAPTERS.get(tool, _adapt_passthrough)
    if adapter is _adapt_passthrough:
        return adapter(raw, route_model)

    try:
        return adapter(raw, route_model)
    except (ValueError, TypeError, KeyError) as exc:
        _warn(f"usage_adapters: failed to parse {tool} stdout: {exc}")
        return _empty_result(assistant_text=raw)
