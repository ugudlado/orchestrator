#!/usr/bin/env python3
"""Query feature_report via duckdb CLI and emit metrics YAML on stdout."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml


def _slug_guard(change_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", change_id))


def _rows_to_metrics(rows: list[dict], source_ts: str) -> dict:
    if not rows:
        raise ValueError("no events for change_id")
    r = rows[0]

    def rint(v):  # noqa: ANN001
        return int(v) if v is not None else 0

    per_agent_tokens = json.dumps(json.loads(r["per_agent_tokens"]), sort_keys=True)
    per_agent_tools = json.dumps(json.loads(r["per_agent_tools"]), sort_keys=True)
    per_tool_uses = json.dumps(json.loads(r["per_tool_uses"]), sort_keys=True)
    per_step_dict = json.loads(r["per_step"])
    review_scores = []
    if r.get("review_scores_json"):
        try:
            review_scores = json.loads(r["review_scores_json"])
        except json.JSONDecodeError:
            review_scores = []
    turns_val = rint(r["turns"])
    return {
        "tokens": {
            "input": rint(r["input_tokens"]),
            "output": rint(r["output_tokens"]),
            "cache_creation": rint(r["cache_creation_input_tokens"]),
            "cache_read": rint(r["cache_read_input_tokens"]),
            "total": rint(r["total_tokens"]),
        },
        "cost": {
            "net_usd": r["cost_usd"],
            "gross_usd": r["gross_usd"],
            "model": r["model"],
            "pricing": {
                "input": r["pricing_input_usd"],
                "output": r["pricing_output_usd"],
                "cache_read": r["pricing_cache_read_usd"],
                "cache_creation": r["pricing_cache_creation_usd"],
            },
        },
        "turns": turns_val,
        "api_calls": turns_val,
        "tool_calls": r["tool_calls_count"],
        "wall_clock_minutes": r["wall_clock_minutes"],
        "category": r["category"],
        "human_interventions": r["human_interventions"],
        "rework_commits": r["rework_commits"],
        "rework_rate": r["rework_rate"],
        "resolution": {
            k: r[k]
            for k in (
                "tasks_total",
                "tasks_planned",
                "tasks_added",
                "tasks_completed",
                "tasks_failed",
                "resolve_rate",
                "pass_at_1",
                "pass_at_2",
                "regressions",
                "regression_rate",
            )
        },
        "retries": {"total": r["retries_total"]},
        "churn": {
            k: r[k]
            for k in ("files_changed", "insertions", "deletions", "total_commits")
        },
        "review_scores": review_scores,
        "review_score_avg": r["review_score_avg"],
        "lint_delta": 0,
        "benchmarks": {
            k: r[k]
            for k in (
                "cost_per_task_usd",
                "cost_per_resolution_usd",
                "tokens_per_task",
                "tokens_per_resolution",
                "input_output_ratio",
                "cache_hit_rate",
            )
        },
        "per_agent_tokens": per_agent_tokens,
        "per_agent_tools": per_agent_tools,
        "per_tool_uses": per_tool_uses,
        "per_step": per_step_dict,
        "source": f"duckdb@{source_ts}",
    }


def main() -> int:
    state_path = os.environ.get("STATE_YAML_PATH", "")
    change_id = os.environ.get("CHANGE_ID", "")
    metrics_db = os.environ.get("METRICS_DB", "")
    for label, val in (
        ("STATE_YAML_PATH", state_path),
        ("CHANGE_ID", change_id),
        ("METRICS_DB", metrics_db),
    ):
        if not val:
            print(f"ERROR: {label} required", file=sys.stderr)
            return 1
    if not os.path.isfile(state_path):
        print(f"ERROR: state.yaml not found at {state_path}", file=sys.stderr)
        return 1
    if not _slug_guard(change_id):
        print(f"ERROR: change_id '{change_id}' violates slug guard", file=sys.stderr)
        return 3

    proc = subprocess.run(
        [
            "duckdb",
            "-readonly",
            "-json",
            metrics_db,
            "-c",
            f"SELECT * FROM feature_report WHERE change_id = '{change_id}'",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"ERROR: duckdb query failed for change_id={change_id}", file=sys.stderr)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return 1
    rows = json.loads(proc.stdout or "[]")
    ts = os.environ.get("COMPUTE_SWE_SOURCE_TS") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    metrics = _rows_to_metrics(rows, ts)
    print(yaml.safe_dump({"metrics": metrics}, sort_keys=True, default_flow_style=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
