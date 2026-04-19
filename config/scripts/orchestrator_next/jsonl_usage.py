"""
Parse Claude Code session JSONL files to extract billing-truth usage.

Claude Code writes every assistant turn to a JSONL file with the full usage
block exactly as returned by the Anthropic API. This module aggregates those
entries into a single dict suitable for `orchestrator record` payloads.

Two entry points:
  - extract_agent_usage(agent_id): one sub-agent's full usage from its JSONL.
  - extract_driver_usage(session_id): the driver (parent) session's usage.

Both return a dict with keys: input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens, duration_ms, model,
tool_calls (dict), turns. Missing fields are None / {} — never synthesized.

JSONL layout (observed on macOS, Claude Code 2026-04):
  ~/.claude/projects/<repo-slug>/<session-uuid>.jsonl
  ~/.claude/projects/<repo-slug>/<driver-session>/subagents/agent-<id>.jsonl

The repo-slug is the absolute repo path with '/' replaced by '-'. Claude Code
picks this path deterministically so consumers can construct it from
os.getcwd() or `git rev-parse --show-toplevel`.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


def _repo_slug(repo_root: str) -> str:
    """Convert an absolute repo path to the Claude Code projects slug.

    Example: /Users/spidey/code/orchestrator → -Users-spidey-code-orchestrator
    """
    return repo_root.replace("/", "-")


def _projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _aggregate(jsonl_path: Path) -> dict[str, Any]:
    """Read a JSONL file and sum usage across all assistant turns with a usage block."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    tool_counter: Counter[str] = Counter()
    models: Counter[str] = Counter()
    turns = 0
    first_ts: str | None = None
    last_ts: str | None = None

    if not jsonl_path.exists():
        return {}

    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "assistant":
                continue
            msg = row.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            turns += 1
            for key in totals:
                val = usage.get(key)
                if isinstance(val, int):
                    totals[key] += val
            model = msg.get("model")
            if model:
                models[model] += int(usage.get("input_tokens") or 0)
            ts = row.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            # Tool uses appear as content blocks with type="tool_use"
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name")
                    if name:
                        tool_counter[name] += 1

    if turns == 0:
        return {}

    result: dict[str, Any] = dict(totals)
    result["turns"] = turns
    result["tool_calls"] = dict(tool_counter)
    # Dominant model (by input tokens) so cost_usd uses a single price block.
    result["model"] = models.most_common(1)[0][0] if models else None
    result["duration_ms"] = _duration_ms(first_ts, last_ts)
    return result


def _duration_ms(first: str | None, last: str | None) -> int | None:
    if not first or not last:
        return None
    # ISO 8601 strings like '2026-04-19T11:24:10.123Z'. Keep it simple — use
    # datetime.fromisoformat after normalising the trailing Z.
    import datetime as dt

    def parse(s: str) -> dt.datetime | None:
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    a, b = parse(first), parse(last)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


def _locate_subagent_jsonl(
    repo_root: str,
    agent_id: str,
    driver_session_hint: str | None = None,
) -> Path | None:
    """Find ~/.claude/projects/<slug>/<driver>/subagents/agent-<id>.jsonl.

    If driver_session_hint is given, look only there. Otherwise scan all
    driver sessions under the project and return the first match.
    """
    slug_dir = _projects_root() / _repo_slug(repo_root)
    if not slug_dir.exists():
        return None
    candidates = (
        [slug_dir / driver_session_hint / "subagents"]
        if driver_session_hint
        else [p for p in slug_dir.iterdir() if p.is_dir() and (p / "subagents").is_dir()]
    )
    for c in candidates:
        sub = c if c.name == "subagents" else c / "subagents"
        if not sub.exists():
            continue
        target = sub / f"agent-{agent_id}.jsonl"
        if target.exists():
            return target
    return None


def _locate_driver_jsonl(repo_root: str, session_id: str) -> Path | None:
    """Find ~/.claude/projects/<slug>/<session_id>.jsonl."""
    slug_dir = _projects_root() / _repo_slug(repo_root)
    target = slug_dir / f"{session_id}.jsonl"
    return target if target.exists() else None


def extract_agent_usage(
    repo_root: str,
    agent_id: str,
    driver_session_hint: str | None = None,
) -> dict[str, Any]:
    """Extract usage for one sub-agent spawn by agent_id.

    Returns a dict suitable for the `usage` key of an `orchestrator record`
    payload. Empty dict if the JSONL cannot be located or has no assistant
    turns (e.g. the agent errored before its first response).
    """
    path = _locate_subagent_jsonl(repo_root, agent_id, driver_session_hint)
    if path is None:
        return {}
    return _aggregate(path)


def extract_driver_usage(repo_root: str, session_id: str) -> dict[str, Any]:
    """Extract usage for the driver (parent) session by session_id.

    Use this at workflow complete-phase to build the synthetic 'driver-loop'
    step_events row.
    """
    path = _locate_driver_jsonl(repo_root, session_id)
    if path is None:
        return {}
    return _aggregate(path)


def discover_subagents(
    repo_root: str,
    driver_session_id: str,
) -> list[str]:
    """List all sub-agent IDs under a driver session's subagents/ dir."""
    slug_dir = _projects_root() / _repo_slug(repo_root)
    sub = slug_dir / driver_session_id / "subagents"
    if not sub.exists():
        return []
    ids = []
    for p in sub.glob("agent-*.jsonl"):
        ids.append(p.stem.removeprefix("agent-"))
    return ids
