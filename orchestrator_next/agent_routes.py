"""Agent → (subprocess tool, model tier) resolution.

In-process replacement for orchestrator_next/scripts/lib/agent-routes.sh.
Reads the `agents:` block from agents.yaml (and an optional override file /
JSON env override), returning the execution config for an agent role.

Precedence (highest wins): ORCHESTRATOR_AGENT_ROUTE_OVERRIDES (JSON env)
> ORCHESTRATOR_AGENTS_CONFIG file > routes_yaml file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


def _agents_map(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    agents = data.get("agents")
    return agents if isinstance(agents, dict) else {}


def resolve_field(agent: str, routes_yaml: str | None, field: str) -> str:
    """Return one route field for `agent` ("" if unset). Mirrors the
    agent_routes_resolve_field precedence from the shell helper."""
    overrides = json.loads(os.environ.get("ORCHESTRATOR_AGENT_ROUTE_OVERRIDES") or "{}")
    agents_config = os.environ.get("ORCHESTRATOR_AGENTS_CONFIG") or ""

    entry: dict[str, Any] = {}
    entry.update(_agents_map(routes_yaml).get(agent) or {})
    entry.update(_agents_map(agents_config).get(agent) or {})
    ov = overrides.get(agent) or {}
    return str(ov.get(field) or entry.get(field) or "")


def resolve_subprocess(agent: str, routes_yaml: str | None) -> str:
    """Tool binary key (e.g. 'claude', 'pi', 'cursor') for an agent role."""
    return resolve_field(agent, routes_yaml, "subprocess")


def resolve_model(agent: str, routes_yaml: str | None) -> str:
    """Model tier (e.g. 'opus', 'sonnet') for an agent role; '' if unset."""
    return resolve_field(agent, routes_yaml, "model")
