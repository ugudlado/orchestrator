"""
Cost report aggregation, rendering, and anomaly detection.

Public API:
  aggregate_feature(db, repo_root, change_id) -> dict
  aggregate_by_scope(db, repo_root, change_id, scope) -> dict
  aggregate_repo(db, repo_basename, since=None, scope=None) -> dict
  render_markdown_feature(data) -> str
  render_markdown_scoped(data, scope) -> str
  render_markdown_repo(data, scope) -> str
  render_json(data) -> str
  _load_agent_tools(agent_name) -> set[str] | None

Design:
  - All SQL is parameterised (no string interpolation of user data).
  - Deterministic ordering: phases by MIN(started_at), agents/models alphabetical,
    tools by call count DESC then name ASC.
  - All aggregation is GROUP BY over step_events / tool_calls — no pre-computed tables.
  - Anomaly detection degrades gracefully: missing file or bad YAML → None → skip.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import yaml

# Slug guard reused for change_id validation in aggregation
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "$0.0000"
    return f"${v:.4f}"


def _fmt_tokens(v: int | None) -> str:
    if v is None:
        return "0"
    return f"{v:,}"


def _fmt_ms(v: int | None) -> str:
    if v is None:
        return "0ms"
    if v >= 60_000:
        return f"{v / 60_000:.1f}m"
    return f"{v / 1000:.1f}s"


# ---------------------------------------------------------------------------
# Agent frontmatter loader
# ---------------------------------------------------------------------------

def _load_agent_tools(agent_name: str) -> set[str] | None:
    """
    Load the tools: list from an agent's YAML frontmatter.

    Search order:
      1. $ORCHESTRATOR_HOME/agents/<agent_name>.md
      2. ~/.claude/agents/<agent_name>.md

    Returns:
      set of tool name strings if found and parseable, else None.
      None means "skip anomaly detection for this agent" (file missing,
      no frontmatter, bad YAML, or no tools: key).
    """
    search_roots = []
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if home:
        search_roots.append(home)
    search_roots.append(os.path.expanduser("~/.claude"))

    for root in search_roots:
        if not root:
            continue
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


# ---------------------------------------------------------------------------
# Aggregation helpers (private)
# ---------------------------------------------------------------------------

def _totals(db, repo_root: str, change_id: str) -> dict:
    sql = """
    SELECT
      COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)        AS cost_usd,
      COALESCE(SUM(gen_ai_usage_input_tokens), 0)      AS input_tokens,
      COALESCE(SUM(gen_ai_usage_output_tokens), 0)     AS output_tokens,
      COALESCE(SUM(duration_ms), 0)                    AS duration_ms,
      COUNT(*)                                          AS step_count
    FROM step_events
    WHERE repo_root = ? AND change_id = ?
    """
    row = db.execute(sql, [repo_root, change_id]).fetchone()
    cost_usd, input_tok, output_tok, dur_ms, step_count = row

    # Rework ratio: SUM(cost WHERE attempt > 1) / SUM(cost); 0.0 on divide-by-zero
    rework_sql = """
    SELECT
      COALESCE(SUM(CASE WHEN attempt > 1 THEN gen_ai_usage_cost_usd ELSE 0.0 END), 0.0),
      COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)
    FROM step_events
    WHERE repo_root = ? AND change_id = ?
    """
    rr = db.execute(rework_sql, [repo_root, change_id]).fetchone()
    rework_numerator, rework_denominator = rr
    rework_ratio = (rework_numerator / rework_denominator) if rework_denominator else 0.0

    return {
        "cost_usd": float(cost_usd),
        "input_tokens": int(input_tok),
        "output_tokens": int(output_tok),
        "duration_ms": int(dur_ms),
        "step_count": int(step_count),
        "rework_ratio": float(rework_ratio),
    }


def _per_phase(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT
      phase,
      COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)      AS cost_usd,
      COALESCE(SUM(gen_ai_usage_input_tokens), 0)    AS input_tokens,
      COALESCE(SUM(gen_ai_usage_output_tokens), 0)   AS output_tokens,
      COALESCE(SUM(duration_ms), 0)                  AS duration_ms,
      COUNT(*)                                        AS step_count,
      MIN(started_at)                                 AS first_seen
    FROM step_events
    WHERE repo_root = ? AND change_id = ?
    GROUP BY phase
    ORDER BY first_seen ASC, phase ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [
        {
            "phase": r[0],
            "cost_usd": float(r[1]),
            "input_tokens": int(r[2]),
            "output_tokens": int(r[3]),
            "duration_ms": int(r[4]),
            "step_count": int(r[5]),
        }
        for r in rows
    ]


def _per_agent(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT
      agent_name,
      COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)      AS cost_usd,
      COALESCE(SUM(gen_ai_usage_input_tokens), 0)    AS input_tokens,
      COALESCE(SUM(gen_ai_usage_output_tokens), 0)   AS output_tokens,
      COALESCE(SUM(duration_ms), 0)                  AS duration_ms,
      COUNT(*)                                        AS step_count
    FROM step_events
    WHERE repo_root = ? AND change_id = ?
    GROUP BY agent_name
    ORDER BY agent_name ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [
        {
            "agent_name": r[0],
            "cost_usd": float(r[1]),
            "input_tokens": int(r[2]),
            "output_tokens": int(r[3]),
            "duration_ms": int(r[4]),
            "step_count": int(r[5]),
        }
        for r in rows
    ]


def _per_model(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT
      COALESCE(gen_ai_request_model, 'unknown')       AS model,
      COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)      AS cost_usd,
      COALESCE(SUM(gen_ai_usage_input_tokens), 0)    AS input_tokens,
      COALESCE(SUM(gen_ai_usage_output_tokens), 0)   AS output_tokens,
      COUNT(*)                                        AS step_count
    FROM step_events
    WHERE repo_root = ? AND change_id = ?
    GROUP BY gen_ai_request_model
    ORDER BY model ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [
        {
            "model": r[0],
            "cost_usd": float(r[1]),
            "input_tokens": int(r[2]),
            "output_tokens": int(r[3]),
            "step_count": int(r[4]),
        }
        for r in rows
    ]


def _native_tools(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT tool_name, COUNT(*) AS calls
    FROM tool_calls
    WHERE repo_root = ? AND change_id = ? AND is_mcp = false
    GROUP BY tool_name
    ORDER BY calls DESC, tool_name ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [{"tool_name": r[0], "calls": int(r[1])} for r in rows]


def _mcp_calls(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT tool_name, COUNT(*) AS calls
    FROM tool_calls
    WHERE repo_root = ? AND change_id = ? AND is_mcp = true
    GROUP BY tool_name
    ORDER BY calls DESC, tool_name ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [{"tool_name": r[0], "calls": int(r[1])} for r in rows]


def _per_agent_tools(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT agent_name, tool_name, COUNT(*) AS calls
    FROM tool_calls
    WHERE repo_root = ? AND change_id = ?
    GROUP BY agent_name, tool_name
    ORDER BY agent_name ASC, calls DESC, tool_name ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [{"agent_name": r[0], "tool_name": r[1], "calls": int(r[2])} for r in rows]


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
            _cache[agent_name] = _load_agent_tools(agent_name)
        allowed = _cache[agent_name]
        if allowed is None:
            continue  # no frontmatter or unparseable — skip
        if tool_name not in allowed:
            result.append({"agent_name": agent_name, "tool_name": tool_name, "calls": int(calls)})

    # Sort deterministically: agent ASC, tool ASC
    result.sort(key=lambda x: (x["agent_name"], x["tool_name"]))
    return result


# ---------------------------------------------------------------------------
# Scope aggregations (for --by step|agent|tool)
# ---------------------------------------------------------------------------

def _by_step(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT
      step_id,
      phase,
      attempt,
      agent_name,
      COALESCE(gen_ai_request_model, 'unknown')       AS model,
      COALESCE(gen_ai_usage_cost_usd, 0.0)            AS cost_usd,
      COALESCE(gen_ai_usage_input_tokens, 0)          AS input_tokens,
      COALESCE(gen_ai_usage_output_tokens, 0)         AS output_tokens,
      COALESCE(duration_ms, 0)                        AS duration_ms,
      status,
      started_at
    FROM step_events
    WHERE repo_root = ? AND change_id = ?
    ORDER BY started_at ASC, step_id ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [
        {
            "step_id": r[0],
            "phase": r[1],
            "attempt": r[2],
            "agent_name": r[3],
            "model": r[4],
            "cost_usd": float(r[5]),
            "input_tokens": int(r[6]),
            "output_tokens": int(r[7]),
            "duration_ms": int(r[8]),
            "status": r[9],
        }
        for r in rows
    ]


def _by_agent_scope(db, repo_root: str, change_id: str) -> list[dict]:
    return _per_agent(db, repo_root, change_id)


def _by_tool(db, repo_root: str, change_id: str) -> list[dict]:
    sql = """
    SELECT tool_name, is_mcp, COUNT(*) AS calls
    FROM tool_calls
    WHERE repo_root = ? AND change_id = ?
    GROUP BY tool_name, is_mcp
    ORDER BY calls DESC, tool_name ASC
    """
    rows = db.execute(sql, [repo_root, change_id]).fetchall()
    return [
        {"tool_name": r[0], "is_mcp": bool(r[1]), "calls": int(r[2])}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Public aggregation API
# ---------------------------------------------------------------------------

def aggregate_feature(db, repo_root: str, change_id: str) -> dict:
    """
    Aggregate all sections for a single change_id (feature-level report).

    Returns a dict with keys:
      totals, per_phase, per_agent, per_model,
      native_tools, mcp_calls, per_agent_tools, anomalies
    """
    return {
        "totals": _totals(db, repo_root, change_id),
        "per_phase": _per_phase(db, repo_root, change_id),
        "per_agent": _per_agent(db, repo_root, change_id),
        "per_model": _per_model(db, repo_root, change_id),
        "native_tools": _native_tools(db, repo_root, change_id),
        "mcp_calls": _mcp_calls(db, repo_root, change_id),
        "per_agent_tools": _per_agent_tools(db, repo_root, change_id),
        "anomalies": _anomalies(db, repo_root, change_id),
    }


def aggregate_by_scope(db, repo_root: str, change_id: str, scope: str) -> dict:
    """
    Aggregate a single focused table by scope: 'step', 'agent', or 'tool'.

    Returns a dict with key 'scope' and 'rows'.
    """
    if scope == "step":
        rows = _by_step(db, repo_root, change_id)
    elif scope == "agent":
        rows = _by_agent_scope(db, repo_root, change_id)
    elif scope == "tool":
        rows = _by_tool(db, repo_root, change_id)
    else:
        raise ValueError(f"Unknown scope: {scope!r}. Must be step|agent|tool.")
    return {"scope": scope, "rows": rows}


def aggregate_repo(db, repo_basename: str, since: str | None = None, scope: str | None = None) -> dict:
    """
    Aggregate across all change_ids whose repo_root basename matches repo_basename.

    scope:
      None / 'feature' → per-feature totals (default)
      'agent'          → aggregated per-agent across all features
      'tool'           → aggregated per-tool across all features
    """
    since_param = since  # ISO string or None

    if scope in (None, "feature"):
        sql = """
        SELECT
          change_id,
          COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)    AS cost_usd,
          COALESCE(SUM(gen_ai_usage_input_tokens), 0)  AS input_tokens,
          COALESCE(SUM(gen_ai_usage_output_tokens), 0) AS output_tokens,
          COUNT(*)                                      AS step_count,
          MIN(started_at)                               AS first_seen
        FROM step_events
        WHERE regexp_extract(repo_root, '[^/]+$') = ?
          AND (? IS NULL OR started_at >= ?)
        GROUP BY change_id
        ORDER BY first_seen ASC, change_id ASC
        """
        rows = db.execute(sql, [repo_basename, since_param, since_param]).fetchall()
        return {
            "scope": scope or "feature",
            "repo_basename": repo_basename,
            "rows": [
                {
                    "change_id": r[0],
                    "cost_usd": float(r[1]),
                    "input_tokens": int(r[2]),
                    "output_tokens": int(r[3]),
                    "step_count": int(r[4]),
                }
                for r in rows
            ],
        }
    elif scope == "agent":
        sql = """
        SELECT
          agent_name,
          COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)    AS cost_usd,
          COALESCE(SUM(gen_ai_usage_input_tokens), 0)  AS input_tokens,
          COALESCE(SUM(gen_ai_usage_output_tokens), 0) AS output_tokens,
          COUNT(*)                                      AS step_count
        FROM step_events
        WHERE regexp_extract(repo_root, '[^/]+$') = ?
          AND (? IS NULL OR started_at >= ?)
        GROUP BY agent_name
        ORDER BY agent_name ASC
        """
        rows = db.execute(sql, [repo_basename, since_param, since_param]).fetchall()
        return {
            "scope": "agent",
            "repo_basename": repo_basename,
            "rows": [
                {
                    "agent_name": r[0],
                    "cost_usd": float(r[1]),
                    "input_tokens": int(r[2]),
                    "output_tokens": int(r[3]),
                    "step_count": int(r[4]),
                }
                for r in rows
            ],
        }
    elif scope == "tool":
        sql = """
        SELECT tc.tool_name, tc.is_mcp, COUNT(*) AS calls
        FROM tool_calls tc
        JOIN step_events se
          ON tc.repo_root = se.repo_root
         AND tc.change_id = se.change_id
         AND tc.phase = se.phase
         AND tc.step_id = se.step_id
         AND tc.attempt = se.attempt
        WHERE regexp_extract(tc.repo_root, '[^/]+$') = ?
          AND (? IS NULL OR se.started_at >= ?)
        GROUP BY tc.tool_name, tc.is_mcp
        ORDER BY calls DESC, tc.tool_name ASC
        """
        rows = db.execute(sql, [repo_basename, since_param, since_param]).fetchall()
        return {
            "scope": "tool",
            "repo_basename": repo_basename,
            "rows": [
                {"tool_name": r[0], "is_mcp": bool(r[1]), "calls": int(r[2])}
                for r in rows
            ],
        }
    else:
        raise ValueError(f"Unknown repo scope: {scope!r}. Must be feature|agent|tool.")


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple markdown table."""
    sep = " | "
    header_row = sep.join(headers)
    divider = sep.join(["---"] * len(headers))
    lines = [f"| {header_row} |", f"| {divider} |"]
    for row in rows:
        lines.append(f"| {sep.join(str(c) for c in row)} |")
    return "\n".join(lines)


def render_markdown_feature(data: dict) -> str:
    """Render the full 8-section feature report as markdown."""
    totals = data["totals"]
    lines = []

    # 1. Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"| --- | --- |")
    lines.append(f"| Total cost | {_fmt_usd(totals['cost_usd'])} |")
    lines.append(f"| Input tokens | {_fmt_tokens(totals['input_tokens'])} |")
    lines.append(f"| Output tokens | {_fmt_tokens(totals['output_tokens'])} |")
    lines.append(f"| Duration | {_fmt_ms(totals['duration_ms'])} |")
    lines.append(f"| Steps | {totals['step_count']} |")
    lines.append(f"| Rework ratio | {totals['rework_ratio']:.1%} |")
    lines.append("")

    # 2. Per-Phase
    lines.append("## Per-Phase")
    lines.append("")
    if data["per_phase"]:
        headers = ["Phase", "Cost", "Input Tok", "Output Tok", "Duration", "Steps"]
        rows = [
            [
                r["phase"],
                _fmt_usd(r["cost_usd"]),
                _fmt_tokens(r["input_tokens"]),
                _fmt_tokens(r["output_tokens"]),
                _fmt_ms(r["duration_ms"]),
                str(r["step_count"]),
            ]
            for r in data["per_phase"]
        ]
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No data._")
    lines.append("")

    # 3. Per-Agent
    lines.append("## Per-Agent")
    lines.append("")
    if data["per_agent"]:
        headers = ["Agent", "Cost", "Input Tok", "Output Tok", "Duration", "Steps"]
        rows = [
            [
                r["agent_name"],
                _fmt_usd(r["cost_usd"]),
                _fmt_tokens(r["input_tokens"]),
                _fmt_tokens(r["output_tokens"]),
                _fmt_ms(r["duration_ms"]),
                str(r["step_count"]),
            ]
            for r in data["per_agent"]
        ]
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No data._")
    lines.append("")

    # 4. Per-Model
    lines.append("## Per-Model")
    lines.append("")
    if data["per_model"]:
        headers = ["Model", "Cost", "Input Tok", "Output Tok", "Steps"]
        rows = [
            [
                r["model"],
                _fmt_usd(r["cost_usd"]),
                _fmt_tokens(r["input_tokens"]),
                _fmt_tokens(r["output_tokens"]),
                str(r["step_count"]),
            ]
            for r in data["per_model"]
        ]
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No data._")
    lines.append("")

    # 5. Native Tools
    lines.append("## Native Tools")
    lines.append("")
    if data["native_tools"]:
        headers = ["Tool", "Calls"]
        rows = [[r["tool_name"], str(r["calls"])] for r in data["native_tools"]]
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No native tool calls._")
    lines.append("")

    # 6. MCP Calls
    lines.append("## MCP Calls")
    lines.append("")
    if data["mcp_calls"]:
        headers = ["Tool", "Calls"]
        rows = [[r["tool_name"], str(r["calls"])] for r in data["mcp_calls"]]
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No MCP calls._")
    lines.append("")

    # 7. Per-Agent Tool Use
    lines.append("## Per-Agent Tool Use")
    lines.append("")
    if data["per_agent_tools"]:
        # Group by agent for sub-tables
        current_agent = None
        agent_rows: list[list[str]] = []
        all_agent_data = data["per_agent_tools"]
        for i, r in enumerate(all_agent_data):
            if r["agent_name"] != current_agent:
                if current_agent is not None and agent_rows:
                    lines.append(_md_table(["Tool", "Calls"], agent_rows))
                    lines.append("")
                current_agent = r["agent_name"]
                lines.append(f"### {current_agent}")
                lines.append("")
                agent_rows = []
            agent_rows.append([r["tool_name"], str(r["calls"])])
        if agent_rows:
            lines.append(_md_table(["Tool", "Calls"], agent_rows))
            lines.append("")
    else:
        lines.append("_No tool call data._")
        lines.append("")

    # 8. Anomalies
    lines.append("## Anomalies")
    lines.append("")
    if data["anomalies"]:
        for a in data["anomalies"]:
            lines.append(
                f"⚠️ {a['agent_name']} used {a['tool_name']} ({a['calls']} calls)"
                f" — not in declared tools list"
            )
        lines.append("")
    else:
        lines.append("_No anomalies detected._")
        lines.append("")

    return "\n".join(lines)


def render_markdown_scoped(data: dict, scope: str) -> str:
    """Render a single focused markdown table for --by step|agent|tool."""
    rows_data = data["rows"]
    lines = []

    if scope == "step":
        lines.append("## By Step")
        lines.append("")
        if rows_data:
            headers = ["Step", "Phase", "Attempt", "Agent", "Model", "Cost", "Input Tok", "Output Tok", "Duration", "Status"]
            rows = [
                [
                    r["step_id"],
                    r["phase"],
                    str(r["attempt"]),
                    r["agent_name"],
                    r["model"],
                    _fmt_usd(r["cost_usd"]),
                    _fmt_tokens(r["input_tokens"]),
                    _fmt_tokens(r["output_tokens"]),
                    _fmt_ms(r["duration_ms"]),
                    r["status"],
                ]
                for r in rows_data
            ]
            lines.append(_md_table(headers, rows))
        else:
            lines.append("_No data._")

    elif scope == "agent":
        lines.append("## By Agent")
        lines.append("")
        if rows_data:
            headers = ["Agent", "Cost", "Input Tok", "Output Tok", "Duration", "Steps"]
            rows = [
                [
                    r["agent_name"],
                    _fmt_usd(r["cost_usd"]),
                    _fmt_tokens(r["input_tokens"]),
                    _fmt_tokens(r["output_tokens"]),
                    _fmt_ms(r["duration_ms"]),
                    str(r["step_count"]),
                ]
                for r in rows_data
            ]
            lines.append(_md_table(headers, rows))
        else:
            lines.append("_No data._")

    elif scope == "tool":
        lines.append("## By Tool")
        lines.append("")
        if rows_data:
            headers = ["Tool", "MCP?", "Calls"]
            rows = [
                [r["tool_name"], "yes" if r["is_mcp"] else "no", str(r["calls"])]
                for r in rows_data
            ]
            lines.append(_md_table(headers, rows))
        else:
            lines.append("_No data._")

    else:
        lines.append(f"_Unknown scope: {scope}_")

    lines.append("")
    return "\n".join(lines)


def render_markdown_repo(data: dict, scope: str | None = None) -> str:
    """Render a repo-level markdown report."""
    rows_data = data["rows"]
    repo = data.get("repo_basename", "")
    lines = []

    effective_scope = scope or data.get("scope", "feature")

    if effective_scope in ("feature", None):
        lines.append(f"## Repo: {repo}")
        lines.append("")
        if rows_data:
            headers = ["Change ID", "Cost", "Input Tok", "Output Tok", "Steps"]
            rows = [
                [
                    r["change_id"],
                    _fmt_usd(r["cost_usd"]),
                    _fmt_tokens(r["input_tokens"]),
                    _fmt_tokens(r["output_tokens"]),
                    str(r["step_count"]),
                ]
                for r in rows_data
            ]
            lines.append(_md_table(headers, rows))
        else:
            lines.append("_No data._")

    elif effective_scope == "agent":
        lines.append(f"## Repo {repo} — By Agent")
        lines.append("")
        if rows_data:
            headers = ["Agent", "Cost", "Input Tok", "Output Tok", "Steps"]
            rows = [
                [
                    r["agent_name"],
                    _fmt_usd(r["cost_usd"]),
                    _fmt_tokens(r["input_tokens"]),
                    _fmt_tokens(r["output_tokens"]),
                    str(r["step_count"]),
                ]
                for r in rows_data
            ]
            lines.append(_md_table(headers, rows))
        else:
            lines.append("_No data._")

    elif effective_scope == "tool":
        lines.append(f"## Repo {repo} — By Tool")
        lines.append("")
        if rows_data:
            headers = ["Tool", "MCP?", "Calls"]
            rows = [
                [r["tool_name"], "yes" if r["is_mcp"] else "no", str(r["calls"])]
                for r in rows_data
            ]
            lines.append(_md_table(headers, rows))
        else:
            lines.append("_No data._")

    lines.append("")
    return "\n".join(lines)


def render_json(data: dict) -> str:
    """
    Render data as a JSON string.

    For feature-level data, ensures the 8 documented top-level keys are present.
    """
    return json.dumps(data, indent=2, sort_keys=True)
