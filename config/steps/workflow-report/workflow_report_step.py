#!/usr/bin/env python3
"""Workflow report step — cost, SWE metrics, learn metrics, and workflow issues in one place."""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Steps lib (cost_summary_relpath, change_id, load)
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Local modules (co-located in this step directory)
_STEP_DIR = Path(__file__).resolve().parent
if str(_STEP_DIR) not in sys.path:
    sys.path.insert(0, str(_STEP_DIR))

from state_yaml import change_id as _change_id, cost_summary_relpath, load as _load_state  # noqa: E402
from _swe_metrics import _rows_to_metrics, _slug_guard  # noqa: E402
from _learn_metrics import _run_query  # noqa: E402

_LOG = "workflow-report"


def _resolve_state(state_path: str, repo_root: str) -> tuple[Path, dict] | tuple[None, None]:
    """Return (resolved_path, state_dict). Handles post-archive moves gracefully."""
    path = Path(state_path)
    if path.is_file():
        return path, _load_state(path)
    # state.yaml was moved by archive-completed-change — find it under archive
    cid_hint = path.parent.name  # spec/changes/<change_id>/state.yaml
    pattern = os.path.join(repo_root, "spec", "changes", "archive", f"*{cid_hint}", "state.yaml")
    for p in sorted(glob.glob(pattern)):
        candidate = Path(p)
        if candidate.is_file():
            return candidate, _load_state(candidate)
    return None, None


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def _run_cost(scripts_dir: str, cid: str, change_dir: Path, state_path: str) -> tuple[str, str]:
    """Run cost-report.sh. Returns (tail_summary, cost_summary_path_rel)."""
    cost_sh = Path(scripts_dir) / "metrics" / "cost-report.sh"
    if not cost_sh.is_file():
        sys.stderr.write(f"{_LOG}: metrics/cost-report.sh not found — skipping cost\n")
        return "", ""

    summary_path = change_dir / "cost-summary.md"
    err_path = change_dir / ".cost-report.err"

    proc = subprocess.run(
        ["bash", str(cost_sh), "--change-id", cid],
        capture_output=True, text=True, env=os.environ.copy(),
    )
    if proc.returncode != 0:
        err_path.write_text(proc.stderr or "", encoding="utf-8")
        sys.stderr.write(f"{_LOG}: cost-report.sh failed\n")
        sys.stderr.write(proc.stderr or "")
        return "", ""

    summary_path.write_text(proc.stdout or "", encoding="utf-8")
    err_path.unlink(missing_ok=True)

    tail_proc = subprocess.run(
        ["bash", str(cost_sh), "--change-id", cid, "--tail"],
        capture_output=True, text=True,
    )
    tail = (tail_proc.stdout or "").strip()
    rel = cost_summary_relpath(state_path, summary_path)
    return tail, rel


# ---------------------------------------------------------------------------
# SWE metrics
# ---------------------------------------------------------------------------

def _run_swe_metrics(cid: str) -> dict:
    """Compute SWE metrics via duckdb. Returns metrics dict or {}."""
    metrics_db = os.environ.get("METRICS_DB", "")
    if not metrics_db or not os.path.isfile(metrics_db):
        return {}
    if not _slug_guard(cid):
        sys.stderr.write(f"{_LOG}: change_id '{cid}' invalid — skipping SWE metrics\n")
        return {}

    proc = subprocess.run(
        ["duckdb", "-readonly", "-json", metrics_db, "-c",
         f"SELECT * FROM feature_report WHERE change_id = '{cid}'"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"{_LOG}: duckdb query failed for {cid}\n")
        return {}

    rows = json.loads(proc.stdout or "[]")
    if not rows:
        return {}

    ts = os.environ.get("COMPUTE_SWE_SOURCE_TS") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        return _rows_to_metrics(rows, ts)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"{_LOG}: SWE metrics compute failed: {exc}\n")
        return {}


# ---------------------------------------------------------------------------
# Learn metrics
# ---------------------------------------------------------------------------

def _run_learn_metrics(scripts_dir: str, repo_root: str, state_path: str) -> dict:
    """Gather DuckDB CSVs for the next learner run. Returns learn_metrics dict."""
    metrics_sh = Path(scripts_dir) / "metrics" / "metrics-query.sh"
    if not metrics_sh.is_file():
        return {"status": "unavailable", "reason": "metrics-query.sh missing"}

    return {
        "scope": os.environ.get("LEARN_SCOPE", "all"),
        "state_yaml_path": state_path,
        "retry_hotspots_csv": _run_query(metrics_sh, repo_root, "retry-hotspots", "--limit",
                                         os.environ.get("LEARN_RETRY_HOTSPOTS_LIMIT", "10")),
        "cycle_count_csv": _run_query(metrics_sh, repo_root, "cycle-count"),
        "recent_features_csv": _run_query(metrics_sh, repo_root, "recent-features", "--limit",
                                          os.environ.get("LEARN_RECENT_FEATURES_LIMIT", "10")),
        "quality_trend_csv": _run_query(metrics_sh, repo_root, "quality-trend", "--limit",
                                        os.environ.get("LEARN_QUALITY_TREND_LIMIT", "5")),
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_report(tail: str, metrics: dict, issues: list) -> None:
    if tail:
        sys.stderr.write(f"\nworkflow complete: {tail}\n")

    if metrics:
        cost = metrics.get("cost") or {}
        tokens = metrics.get("tokens") or {}
        res = metrics.get("resolution") or {}
        sys.stderr.write(
            f"  cost: ${cost.get('net_usd', 0):.4f}  "
            f"tokens: {tokens.get('total', 0):,}  "
            f"tasks: {res.get('tasks_completed', 0)}/{res.get('tasks_total', 0)}  "
            f"retries: {(metrics.get('retries') or {}).get('total', 0)}\n"
        )

    if issues:
        sys.stderr.write(f"\n## Workflow issues this run ({len(issues)})\n\n")
        sys.stderr.write("| Severity | Category | Detail | Fix direction |\n")
        sys.stderr.write("|---|---|---|---|\n")
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            sev = issue.get("severity") or "—"
            cat = issue.get("category") or "—"
            det = (issue.get("detail") or "").replace("\n", " ")[:120]
            fix = (issue.get("fix_direction") or "—").replace("\n", " ")
            sys.stderr.write(f"| {sev} | {cat} | {det} | {fix} |\n")
        sys.stderr.write("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    repo_root = os.environ.get("REPO_ROOT", "")

    if not state_path or not scripts_dir:
        sys.stderr.write(f"error: ORCHESTRATOR_STATE_YAML_PATH and ORCHESTRATOR_SCRIPTS_DIR required\n")
        return 1

    path, state = _resolve_state(state_path, repo_root)
    if path is None:
        sys.stderr.write(f"{_LOG}: state.yaml not found at {state_path} or in archive\n")
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing state.yaml"}}))
        return 1

    cid = _change_id(state)
    if not cid:
        sys.stderr.write(f"{_LOG}: change_id missing in state.yaml\n")
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing change_id"}}))
        return 1

    change_dir = path.parent

    tail, cost_path = _run_cost(scripts_dir, cid, change_dir, str(path))
    swe_metrics = _run_swe_metrics(cid)
    learn_metrics = _run_learn_metrics(scripts_dir, repo_root, str(path))
    issues = state.get("workflow_issues") or []

    _render_report(tail, swe_metrics, issues)

    outputs: dict = {"tail_summary": tail}
    if cost_path:
        outputs["cost_summary_path"] = cost_path
    if issues:
        outputs["workflow_issues_count"] = len(issues)

    print(json.dumps({
        "status": "completed",
        "outputs": outputs,
        "metrics": swe_metrics or None,
        "learn_metrics": learn_metrics,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
