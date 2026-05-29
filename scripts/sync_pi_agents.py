#!/usr/bin/env python3
"""Generate Pi-compatible subagent files from orchestrator agents/*.md."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
MCP_CLAUDE_RE = re.compile(r"^mcp__(.+?)__(.+)$")

PI_KNOWN_FIELDS = {
    "name",
    "package",
    "description",
    "tools",
    "model",
    "fallbackModels",
    "thinking",
    "systemPromptMode",
    "inheritProjectContext",
    "inheritSkills",
    "defaultContext",
    "skill",
    "skills",
    "extensions",
    "output",
    "defaultReads",
    "defaultProgress",
    "interactive",
    "maxSubagentDepth",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="agents/*.md source dir")
    parser.add_argument("--config", type=Path, required=True, help="config/pi-agents.yaml")
    parser.add_argument(
        "--out",
        type=Path,
        action="append",
        required=True,
        help="Output directory (repeatable)",
    )
    return parser.parse_args()


def split_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    body = text[match.end() :]
    return frontmatter, body


def normalize_description(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return " ".join(str(value).split())


def normalize_tools(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("["):
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("tools JSON must be a list")
            return [str(item) for item in parsed]
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"unsupported tools value: {value!r}")


def infer_mcp_tool(claude_tool: str, mcp_tool_map: dict[str, str]) -> str | None:
    if claude_tool in mcp_tool_map:
        return mcp_tool_map[claude_tool]
    match = MCP_CLAUDE_RE.match(claude_tool)
    if not match:
        return None
    server = match.group(1).replace("_", "-")
    tool = match.group(2).replace("_", "-")
    return f"mcp:{server}/{tool}"


def convert_tools(
    claude_tools: list[str],
    *,
    tool_map: dict[str, str],
    skip_tools: set[str],
    mcp_tool_map: dict[str, str],
    extra_tools: list[str] | None = None,
) -> list[str]:
    converted: list[str] = []
    seen: set[str] = set()

    def add(tool: str) -> None:
        if tool and tool not in seen:
            seen.add(tool)
            converted.append(tool)

    for tool in claude_tools:
        if tool in skip_tools:
            continue
        if tool in tool_map:
            add(tool_map[tool])
            continue
        if tool.startswith("mcp__"):
            mapped = infer_mcp_tool(tool, mcp_tool_map)
            if mapped:
                add(mapped)
            continue
        lowered = tool.lower()
        if lowered in PI_KNOWN_FIELDS or lowered in {"read", "write", "edit", "grep", "find", "ls", "bash"}:
            add(lowered)

    for tool in extra_tools or []:
        add(tool)

    if not converted:
        converted = ["read", "grep", "find", "ls", "bash"]
    return converted


def yaml_quote(value: str) -> str:
    if not value:
        return '""'
    if re.search(r'[:#\[\]{},"\'&*!?|>]', value) or value != value.strip():
        return json.dumps(value)
    return value


def render_pi_agent(frontmatter: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if key == "tools" and isinstance(value, list):
            lines.append(f"tools: {', '.join(value)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            continue
        else:
            lines.append(f"{key}: {yaml_quote(str(value))}")
    lines.append("---")
    lines.append(body.lstrip("\n"))
    return "\n".join(lines).rstrip() + "\n"


def build_pi_frontmatter(
    source: dict[str, Any],
    config: dict[str, Any],
    agent_name: str,
) -> dict[str, Any]:
    defaults = config.get("defaults", {})
    agent_overrides = config.get("agents", {}).get(agent_name, {})
    tool_map = config.get("tool_map", {})
    skip_tools = set(config.get("skip_tools", []))
    mcp_tool_map = config.get("mcp_tool_map", {})

    description = normalize_description(source.get("description"))
    if not description:
        raise ValueError(f"{agent_name}: missing description")

    tools = convert_tools(
        normalize_tools(source.get("tools")),
        tool_map=tool_map,
        skip_tools=skip_tools,
        mcp_tool_map=mcp_tool_map,
        extra_tools=agent_overrides.get("extra_tools"),
    )

    pi_frontmatter: dict[str, Any] = {
        "name": agent_name,
        "description": description,
        "tools": tools,
        "systemPromptMode": agent_overrides.get(
            "systemPromptMode", defaults.get("systemPromptMode", "replace")
        ),
        "inheritProjectContext": agent_overrides.get(
            "inheritProjectContext", defaults.get("inheritProjectContext", True)
        ),
        "inheritSkills": agent_overrides.get(
            "inheritSkills", defaults.get("inheritSkills", False)
        ),
    }

    for key in ("defaultContext", "thinking", "model", "defaultReads", "defaultProgress"):
        if key in agent_overrides:
            pi_frontmatter[key] = agent_overrides[key]
        elif key in defaults:
            pi_frontmatter[key] = defaults[key]

    return pi_frontmatter


def sync_agent(source_path: Path, config: dict[str, Any], out_dirs: list[Path]) -> None:
    source_frontmatter, body = split_markdown(source_path)
    agent_name = str(source_frontmatter.get("name") or source_path.stem)
    pi_frontmatter = build_pi_frontmatter(source_frontmatter, config, agent_name)
    rendered = render_pi_agent(pi_frontmatter, body)

    for out_dir in out_dirs:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / source_path.name
        if out_path.is_symlink() or out_path.exists():
            out_path.unlink()
        out_path.write_text(rendered, encoding="utf-8")


def main() -> int:
    args = parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    # ORC-105: pi overrides now live under a `pi:` key in the merged
    # config/agents.yaml. Unwrap it; fall back to the flat top-level shape
    # for the legacy standalone config/pi-agents.yaml.
    config = config.get("pi", config)
    sources = sorted(args.source.glob("*.md"))
    if not sources:
        print(f"error: no agent files found in {args.source}", file=sys.stderr)
        return 1

    for source_path in sources:
        sync_agent(source_path, config, args.out)

    print(f"  generated {len(sources)} Pi agent file(s) -> {', '.join(str(p) for p in args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
