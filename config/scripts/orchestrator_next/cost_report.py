"""
Anomaly detection helpers for cost reporting.

Public API:
  _anomalies(db, repo_root, change_id) -> list[dict]
  _step_allowlist_anomalies(db, repo_root, change_id) -> list[dict]

Note: Aggregation, rendering, and formatting functions were removed in T-12
(report-views-retire-cli). The DuckDB views in views/ now handle all
aggregation; scripts/cost-report.sh handles all rendering.

Design:
  - Anomaly detection degrades gracefully: missing file or bad YAML → None → skip.
"""
from __future__ import annotations

import yaml

from .resolver import load_agent_tools
from .parser import _load_contract, ContractError, StepContract


def _anomalies(db, repo_root: str, change_id: str) -> list[dict]:
    """
    Find (agent, tool) pairs where the tool is not in the agent's declared frontmatter.
    Returns a list of dicts with keys: agent_name, tool_name, calls.
    Agents whose files are missing or unparseable are silently skipped.
    """
    sql = """
    SELECT agent_name, tool_name, COUNT(*) AS calls
    FROM tool_calls
    WHERE repo_root = ? AND change_id = ?
    GROUP BY agent_name, tool_name
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()

    result = []
    # Cache to avoid reading the same file multiple times
    _cache: dict[str, set[str] | None] = {}
    for agent_name, tool_name, calls in rows:
        if agent_name not in _cache:
            _cache[agent_name] = load_agent_tools(agent_name)
        allowed = _cache[agent_name]
        if allowed is None:
            continue  # no frontmatter or unparseable — skip
        if tool_name not in allowed:
            result.append({"agent_name": agent_name, "tool_name": tool_name, "calls": int(calls)})

    # Sort deterministically: agent ASC, tool ASC
    result.sort(key=lambda x: (x["agent_name"], x["tool_name"]))
    return result


def _step_allowlist_anomalies(db, repo_root: str, change_id: str) -> list[dict]:
    """
    Find (phase, step_id, agent, tool) rows where the tool is not in the step's
    declared allowed_tools list — but only when that list is non-empty.

    Rows where the contract:
      - is missing at report time → silently skipped
      - has empty allowed_tools → skipped (no restriction)

    Returns a list of dicts: agent_name, step_id, tool_name, calls.
    """
    sql = """
    SELECT phase, step_id, agent_name, tool_name, COUNT(*) AS calls
    FROM tool_calls
    WHERE repo_root = ? AND change_id = ?
    GROUP BY phase, step_id, agent_name, tool_name
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()

    result = []
    # Cache loaded contracts: (phase, step_id) -> StepContract | None
    _contract_cache: dict[tuple[str, str], StepContract | None] = {}

    for phase, step_id, agent_name, tool_name, calls in rows:
        cache_key = (phase, step_id)
        if cache_key not in _contract_cache:
            try:
                contract = _load_contract(step_id, "")
            except (FileNotFoundError, ContractError, yaml.YAMLError, OSError):
                contract = None
            _contract_cache[cache_key] = contract

        contract = _contract_cache[cache_key]
        if contract is None:
            continue  # missing or bad contract — skip silently
        if not contract.allowed_tools:
            continue  # no restriction declared — skip
        if tool_name not in contract.allowed_tools:
            result.append({
                "agent_name": agent_name,
                "step_id": step_id,
                "tool_name": tool_name,
                "calls": int(calls),
            })

    # Sort deterministically: step_id ASC, agent ASC, tool ASC
    result.sort(key=lambda x: (x["step_id"], x["agent_name"], x["tool_name"]))
    return result
