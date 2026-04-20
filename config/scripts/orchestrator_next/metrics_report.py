"""
Metrics report composition — joins step_events + feature_metrics + pricing into
a flat dict matching metrics-schema.md field registry for schema=feature.

Public API:
  aggregate_metrics(db, repo_root, change_id) -> dict

Design:
  - Reuses _totals() from cost_report.py (no duplication).
  - Reads feature_metrics for resolution/churn/retries/reviews/wall_clock.
  - Reads feature_complexity for category override (falls back to feature_metrics.schema_name).
  - per_agent_tokens and per_agent_tools are stringified JSON scalars
    (register-repo.sh reads them via yq -p=json — must remain strings).
  - benchmarks computed from the joined data with zero-division guards.
  - All SQL is parameterised — no string interpolation of user data.
"""
from __future__ import annotations

import json
import math
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_feature_metrics(db, repo_root: str, change_id: str) -> dict | None:
    """Fetch one row from feature_metrics, or None if absent."""
    try:
        row = db.execute(
            "SELECT "
            "  schema_name, "
            "  tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed, "
            "  resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate, "
            "  retries_total, human_interventions, "
            "  files_changed, insertions, deletions, total_commits, rework_commits, rework_rate, "
            "  review_scores_json, review_score_avg, "
            "  wall_clock_minutes "
            "FROM feature_metrics "
            "WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()
    except Exception:
        return None

    if row is None:
        return None

    return {
        "schema_name":        row[0],
        "tasks_total":        row[1],
        "tasks_planned":      row[2],
        "tasks_added":        row[3],
        "tasks_completed":    row[4],
        "tasks_failed":       row[5],
        "resolve_rate":       row[6],
        "pass_at_1":          row[7],
        "pass_at_2":          row[8],
        "regressions":        row[9],
        "regression_rate":    row[10],
        "retries_total":      row[11],
        "human_interventions": row[12],
        "files_changed":      row[13],
        "insertions":         row[14],
        "deletions":          row[15],
        "total_commits":      row[16],
        "rework_commits":     row[17],
        "rework_rate":        row[18],
        "review_scores_json": row[19],
        "review_score_avg":   row[20],
        "wall_clock_minutes": row[21],
    }


def _fetch_feature_complexity(db, repo_root: str, change_id: str) -> dict | None:
    """Fetch one row from feature_complexity, or None if absent."""
    try:
        row = db.execute(
            "SELECT complexity, schema_name FROM feature_complexity "
            "WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()
    except Exception:
        return None

    if row is None:
        return None

    return {"complexity": row[0], "schema_name": row[1]}


def _resolve_schema(fm_row: dict | None, fc_row: dict | None) -> str:
    """Resolve the schema name from feature_metrics or feature_complexity."""
    if fm_row and fm_row.get("schema_name"):
        return fm_row["schema_name"]
    if fc_row and fc_row.get("schema_name"):
        return fc_row["schema_name"]
    return "feature"


def _count_tool_calls(db, repo_root: str, change_id: str) -> int:
    """Return total tool invocations from tool_calls table."""
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _per_step_rollup(db, repo_root: str, change_id: str) -> dict:
    """
    Return per-step aggregates: {step_id: {total_tokens, tool_uses, duration_ms, executions}}.

    total_tokens = input_tokens + output_tokens (BIGINT safe sum).
    tool_uses = COUNT of tool_calls rows for this step.
    duration_ms = SUM of step_events.duration_ms per step_id.
    executions = COUNT(*) of step_events rows per step_id (retry-inclusive).
    """
    try:
        rows = db.execute(
            """
            SELECT
              se.step_id,
              COALESCE(SUM(se.input_tokens), 0) + COALESCE(SUM(se.output_tokens), 0) AS total_tokens,
              COALESCE(SUM(se.duration_ms), 0) AS duration_ms,
              COUNT(*) AS executions
            FROM step_events se
            WHERE se.repo_root = ? AND se.change_id = ?
            GROUP BY se.step_id
            ORDER BY se.step_id ASC
            """,
            [repo_root, change_id],
        ).fetchall()
    except Exception:
        return {}

    # Separately get tool_uses per step_id
    try:
        tc_rows = db.execute(
            """
            SELECT step_id, COUNT(*) AS tool_uses
            FROM tool_calls
            WHERE repo_root = ? AND change_id = ?
            GROUP BY step_id
            """,
            [repo_root, change_id],
        ).fetchall()
        tool_uses_map = {r[0]: int(r[1]) for r in tc_rows}
    except Exception:
        tool_uses_map = {}

    result: dict[str, Any] = {}
    for row in rows:
        step_id = row[0]
        result[step_id] = {
            "total_tokens": int(row[1]) if row[1] is not None else 0,
            "tool_uses":    tool_uses_map.get(step_id, 0),
            "duration_ms":  int(row[2]) if row[2] is not None else 0,
            "executions":   int(row[3]) if row[3] is not None else 0,
        }
    return result


def _build_per_agent_tokens_str(db, repo_root: str, change_id: str) -> str:
    """
    Build per_agent_tokens as a stringified JSON dict.

    Shape: {agent_name: {total_tokens, input_tokens, output_tokens, cost_usd, duration_ms, step_count}}

    Must be a JSON string (not an object) because register-repo.sh reads it via
    `yq -p=json` on the embedded string value.
    """
    try:
        rows = db.execute(
            """
            SELECT
              agent_name,
              COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) AS total_tokens,
              COALESCE(SUM(input_tokens), 0) AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
              COALESCE(SUM(duration_ms), 0) AS duration_ms,
              COUNT(*) AS step_count
            FROM step_events
            WHERE repo_root = ? AND change_id = ?
            GROUP BY agent_name
            ORDER BY agent_name ASC
            """,
            [repo_root, change_id],
        ).fetchall()
    except Exception:
        return "{}"

    result: dict[str, Any] = {}
    for row in rows:
        result[row[0]] = {
            "total_tokens": int(row[1]),
            "input_tokens": int(row[2]),
            "output_tokens": int(row[3]),
            "cost_usd":     float(row[4]),
            "duration_ms":  int(row[5]),
            "step_count":   int(row[6]),
        }
    return json.dumps(result, sort_keys=True)


def _build_per_tool_uses_str(db, repo_root: str, change_id: str) -> str:
    """
    Build per_tool_uses as a stringified JSON dict.

    Shape: {tool_name: total_count, ...}

    Aggregates tool_calls_json across all step_events rows for the change_id.
    Must be a JSON string (not an object) — same rationale as per_agent_tokens.
    """
    try:
        rows = db.execute(
            "SELECT tool_calls_json FROM step_events "
            "WHERE repo_root = ? AND change_id = ? AND tool_calls_json IS NOT NULL",
            [repo_root, change_id],
        ).fetchall()
    except Exception:
        return "{}"

    tool_counts: dict[str, int] = {}
    for (tcj,) in rows:
        try:
            d = json.loads(tcj)
            if isinstance(d, dict):
                for tool, count in d.items():
                    tool_counts[tool] = tool_counts.get(tool, 0) + (count if isinstance(count, int) else 0)
        except (json.JSONDecodeError, TypeError):
            continue

    return json.dumps(tool_counts, sort_keys=True) if tool_counts else "{}"


def _build_per_agent_tools_str(db, repo_root: str, change_id: str) -> str:
    """
    Build per_agent_tools as a stringified JSON dict.

    Shape: {agent_name: {tool_name: count, ...}}

    Must be a JSON string (not an object) — same rationale as per_agent_tokens.
    """
    try:
        rows = db.execute(
            """
            SELECT agent_name, tool_name, COUNT(*) AS calls
            FROM tool_calls
            WHERE repo_root = ? AND change_id = ?
            GROUP BY agent_name, tool_name
            ORDER BY agent_name ASC, calls DESC, tool_name ASC
            """,
            [repo_root, change_id],
        ).fetchall()
    except Exception:
        return "{}"

    result: dict[str, Any] = {}
    for row in rows:
        agent = row[0]
        tool = row[1]
        calls = int(row[2])
        if agent not in result:
            result[agent] = {}
        result[agent][tool] = calls
    return json.dumps(result, sort_keys=True)


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Divide numerator by denominator, returning default on zero division."""
    if denominator == 0:
        return default
    return numerator / denominator


def _compute_benchmarks(totals: dict, fm_row: dict | None) -> dict:
    """Compute benchmark fields from totals + feature_metrics data."""
    net_usd = totals.get("cost_usd") or 0.0
    input_tok = totals.get("input_tokens") or 0
    output_tok = totals.get("output_tokens") or 0
    cache_create = totals.get("cache_creation_input_tokens") or 0
    cache_read = totals.get("cache_read_input_tokens") or 0
    total_tokens = input_tok + output_tok + cache_create

    tasks_total = (fm_row.get("tasks_total") if fm_row else None) or 0
    tasks_completed = (fm_row.get("tasks_completed") if fm_row else None) or 0

    # cache_hit_rate: cache_read / (input + cache_creation + cache_read)
    denom_cache = input_tok + cache_create + cache_read
    cache_hit_rate = _safe_div(cache_read, denom_cache)

    # input_output_ratio: (input + cache_creation) / output
    input_output_ratio = _safe_div(input_tok + cache_create, output_tok)

    return {
        "cost_per_task_usd":       _safe_div(net_usd, tasks_total),
        "cost_per_resolution_usd": _safe_div(net_usd, tasks_completed),
        "tokens_per_task":         int(_safe_div(total_tokens, tasks_total)),
        "tokens_per_resolution":   int(_safe_div(total_tokens, tasks_completed)),
        "input_output_ratio":      round(input_output_ratio, 4),
        "cache_hit_rate":          round(cache_hit_rate, 4),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def aggregate_metrics(db, repo_root: str, change_id: str) -> dict:
    """
    Return a flat dict matching metrics-schema.md field registry for schema=feature.

    Composes:
      - _totals() from cost_report (tokens, cost, turns, gross_usd, model, pricing)
      - feature_metrics row (resolution, churn, retries, reviews, wall_clock)
      - feature_complexity row (complexity bucket)
      - per_step rollup from step_events GROUP BY step_id
      - per_agent_tokens and per_agent_tools as stringified JSON scalars

    Args:
        db:        open duckdb.DuckDBPyConnection (schema already ensured)
        repo_root: absolute path to the repo root
        change_id: feature change identifier

    Returns:
        dict — nested, keyed to metrics-schema.md shape
    """
    from orchestrator_next.cost_report import _totals

    # --- Raw data sources ---
    totals = _totals(db, repo_root, change_id)
    fm_row = _fetch_feature_metrics(db, repo_root, change_id)
    fc_row = _fetch_feature_complexity(db, repo_root, change_id)
    schema = _resolve_schema(fm_row, fc_row)

    input_tok    = totals.get("input_tokens") or 0
    output_tok   = totals.get("output_tokens") or 0
    cache_create = totals.get("cache_creation_input_tokens") or 0
    cache_read   = totals.get("cache_read_input_tokens") or 0
    total_tokens = input_tok + output_tok + cache_create

    tool_calls_count = _count_tool_calls(db, repo_root, change_id)
    per_step = _per_step_rollup(db, repo_root, change_id)
    per_agent_tokens_str = _build_per_agent_tokens_str(db, repo_root, change_id)
    per_agent_tools_str  = _build_per_agent_tools_str(db, repo_root, change_id)
    per_tool_uses_str    = _build_per_tool_uses_str(db, repo_root, change_id)

    # --- review_scores: decode JSON string from feature_metrics ---
    review_scores: list = []
    if fm_row and fm_row.get("review_scores_json"):
        try:
            review_scores = json.loads(fm_row["review_scores_json"])
        except (json.JSONDecodeError, TypeError):
            review_scores = []

    # --- Compose result ---
    result: dict[str, Any] = {
        "tokens": {
            "input":          input_tok,
            "output":         output_tok,
            "cache_creation": cache_create,
            "cache_read":     cache_read,
            "total":          total_tokens,
        },
        "cost": {
            "net_usd":   totals.get("cost_usd") or 0.0,
            "gross_usd": totals.get("gross_usd") or 0.0,
            "model":     totals.get("model"),
            "pricing":   totals.get("pricing") or {
                "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
            },
        },
        "turns":       totals.get("turns") or 0,
        "api_calls":   totals.get("turns") or 0,
        "tool_calls":  tool_calls_count,
        "wall_clock_minutes": (fm_row.get("wall_clock_minutes") if fm_row else None),
        "category":    schema,
        "human_interventions": (
            fm_row.get("human_interventions") if fm_row else None
        ),
        "rework_commits": (fm_row.get("rework_commits") if fm_row else None),
        "rework_rate":    (fm_row.get("rework_rate") if fm_row else None),
        "resolution": {
            "tasks_total":      (fm_row.get("tasks_total")     if fm_row else None),
            "tasks_planned":    (fm_row.get("tasks_planned")   if fm_row else None),
            "tasks_added":      (fm_row.get("tasks_added")     if fm_row else None),
            "tasks_completed":  (fm_row.get("tasks_completed") if fm_row else None),
            "tasks_failed":     (fm_row.get("tasks_failed")    if fm_row else None),
            "resolve_rate":     (fm_row.get("resolve_rate")    if fm_row else None),
            "pass_at_1":        (fm_row.get("pass_at_1")       if fm_row else None),
            "pass_at_2":        (fm_row.get("pass_at_2")       if fm_row else None),
            "regressions":      (fm_row.get("regressions")     if fm_row else None),
            "regression_rate":  (fm_row.get("regression_rate") if fm_row else None),
        },
        "retries": {
            "total": (fm_row.get("retries_total") if fm_row else None),
        },
        "churn": {
            "files_changed": (fm_row.get("files_changed")  if fm_row else None),
            "insertions":    (fm_row.get("insertions")     if fm_row else None),
            "deletions":     (fm_row.get("deletions")      if fm_row else None),
            "total_commits": (fm_row.get("total_commits")  if fm_row else None),
        },
        "review_scores":   review_scores,
        "review_score_avg": (fm_row.get("review_score_avg") if fm_row else None),
        "lint_delta":   0,
        "benchmarks": _compute_benchmarks(totals, fm_row),
        "per_agent_tokens": per_agent_tokens_str,
        "per_agent_tools":  per_agent_tools_str,
        "per_tool_uses":    per_tool_uses_str,
        "per_step": per_step,
    }

    return result


def render_metrics_json(data: dict) -> str:
    """Render metrics data as a JSON string with sorted keys and 2-space indent."""
    return json.dumps(data, indent=2, sort_keys=True)


def render_metrics_md(data: dict) -> str:
    """Render a minimal markdown summary of metrics data."""
    lines = ["## Metrics Summary", ""]
    tokens = data.get("tokens") or {}
    cost = data.get("cost") or {}
    lines.append(f"| Metric | Value |")
    lines.append(f"| --- | --- |")
    lines.append(f"| Category | {data.get('category', '—')} |")
    lines.append(f"| Tokens (total) | {tokens.get('total', 0):,} |")
    lines.append(f"| Cost (net) | ${cost.get('net_usd', 0):.4f} |")
    lines.append(f"| Cost (gross) | ${cost.get('gross_usd', 0):.4f} |")
    lines.append(f"| Model | {cost.get('model', '—')} |")
    lines.append(f"| Turns | {data.get('turns', 0)} |")
    lines.append(f"| Tool calls | {data.get('tool_calls', 0)} |")
    lines.append(f"| Wall-clock (min) | {data.get('wall_clock_minutes', '—')} |")

    res = data.get("resolution") or {}
    if res.get("tasks_total") is not None:
        lines.append("")
        lines.append("### Resolution")
        lines.append("")
        lines.append(f"| tasks_total | {res.get('tasks_total')} |")
        lines.append(f"| tasks_completed | {res.get('tasks_completed')} |")
        lines.append(f"| resolve_rate | {res.get('resolve_rate')} |")

    lines.append("")
    return "\n".join(lines)
