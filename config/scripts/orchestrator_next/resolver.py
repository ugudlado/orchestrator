"""
Shared resolver for agent frontmatter tool sets.

Public API:
  load_agent_tools(agent_name: str) -> set[str] | None

Extracted from cost_report._load_agent_tools (HL-295) so both dispatch.py
and cost_report.py can reuse the same search/parse logic.
"""
from __future__ import annotations

import os
import re

import yaml


def load_agent_tools(agent_name: str) -> set[str] | None:
    """
    Load the tools: list from an agent's YAML frontmatter.

    Search order:
      1. $ORCHESTRATOR_HOME/agents/<agent_name>.md
      2. ~/.claude/agents/<agent_name>.md

    Returns:
      set of tool name strings if found and parseable, else None.
      None means "skip tool resolution for this agent" (file missing,
      no frontmatter, bad YAML, or no tools: key, or tools: is not a list).
    """
    search_roots = []
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if home:
        search_roots.append(home)
    search_roots.append(os.path.expanduser("~/.claude"))

    for root in search_roots:
        path = os.path.join(root, "agents", f"{agent_name}.md")
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, "r", encoding="utf-8").read()
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            return None
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None
        tools = fm.get("tools")
        if not isinstance(tools, list):
            return None
        return set(str(t) for t in tools)
    return None
