"""
Shared resolver for agent tool sets.

Public API:
  load_agent_tools(agent_name: str) -> set[str] | None

Tools are documented as comments in config/agents.yaml but not enforced at
dispatch time. This function always returns None (no restriction).
Kept for API compatibility with dispatch.py and cost_report.py.
"""
from __future__ import annotations


def load_agent_tools(agent_name: str) -> set[str] | None:
    return None
