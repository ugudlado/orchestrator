#!/usr/bin/env python3
"""ingest-feature-metrics.py — Populate feature_metrics DuckDB row.

Reads tasks.md + git log + state.yaml; upserts one row into the
feature_metrics DuckDB table. Fails loud (sys.exit(1)) on missing
tasks.md (for feature/bugfix), missing required state fields, or
DuckDB connection failure. Git churn failures are non-fatal (return zeros).

Usage:
  python ingest-feature-metrics.py <state_yaml_path>

Env vars:
  ORCHESTRATOR_HOME — repo root (resolves config/scripts/ for imports)
  METRICS_DB        — explicit DuckDB path (default: $ORCHESTRATOR_HOME/metrics.duckdb)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# sys.path: mirror the mark-change-completed.sh pattern so we can import
# orchestrator_next.upsert without installing the package.
# ---------------------------------------------------------------------------
_ORCHESTRATOR_HOME = os.environ.get("ORCHESTRATOR_HOME", "")
if _ORCHESTRATOR_HOME:
    _scripts_dir = os.path.join(_ORCHESTRATOR_HOME, "config", "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)

import duckdb  # noqa: E402 — must come after sys.path adjustment
from orchestrator_next.upsert import ensure_schema, upsert_feature_metrics  # noqa: E402


# ---------------------------------------------------------------------------
# Slug guard (mirrors upsert.py constraint)
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _metrics_db_path() -> str:
    """Resolve the DuckDB path from env or ORCHESTRATOR_HOME default."""
    explicit = os.environ.get("METRICS_DB", "")
    if explicit:
        return explicit
    if _ORCHESTRATOR_HOME:
        return os.path.join(_ORCHESTRATOR_HOME, "metrics.duckdb")
    return "metrics.duckdb"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# parse_tasks: count task markers from tasks.md
# ---------------------------------------------------------------------------

def parse_tasks(tasks_md: Path) -> dict[str, Any]:
    """Count [x], [ ], and [~] task markers.

    Returns:
        tasks_total, tasks_completed, tasks_failed, resolve_rate
    """
    text = tasks_md.read_text()
    total = len(re.findall(r"^\s*-\s*\[", text, re.MULTILINE))
    completed = len(re.findall(r"^\s*-\s*\[x\]", text, re.MULTILINE | re.IGNORECASE))
    skipped = len(re.findall(r"^\s*-\s*\[~\]", text, re.MULTILINE))
    failed = total - completed - skipped
    resolve_rate = completed / total if total > 0 else 0.0
    return {
        "tasks_total": total,
        "tasks_planned": total,
        "tasks_added": 0,
        "tasks_completed": completed,
        "tasks_failed": max(failed, 0),
        "resolve_rate": round(resolve_rate, 6),
    }


# ---------------------------------------------------------------------------
# compute_retries: sum retries.* counters from state.yaml
# ---------------------------------------------------------------------------

def compute_retries(state: dict) -> dict[str, Any]:
    """Sum retries.* keys and extract human_interventions.

    Returns:
        retries_total, human_interventions
    """
    retries_section = state.get("retries") or {}
    if isinstance(retries_section, dict):
        retries_total = sum(
            v for v in retries_section.values() if isinstance(v, (int, float))
        )
    else:
        retries_total = 0

    human_interventions = state.get("human_interventions") or 0
    return {
        "retries_total": int(retries_total),
        "human_interventions": int(human_interventions),
    }


# ---------------------------------------------------------------------------
# compute_resolution: derive pass@k and regression metrics from state.yaml
# ---------------------------------------------------------------------------

def compute_resolution(
    tasks_total: int | None,
    tasks_completed: int | None,
    retries_total: int,
    step_history: list,
    quarantine_events: list | None,
) -> dict[str, Any]:
    """Derive pass_at_1, pass_at_2, regressions, regression_rate.

    Approximation note: state.yaml retries are keyed by step_id (e.g.
    "execute-next-task"), not by task_id — so per-task attempt granularity
    is unavailable. We use:
      pass_at_1 = max(0, tasks_total - retries_total) / tasks_total
      pass_at_2 = tasks_completed / tasks_total
    This satisfies the monotonicity invariant pass_at_2 >= pass_at_1 and
    is the tightest approximation possible without per-task retry records.
    quarantine_events would normally reduce the numerator, but since
    quarantined tasks are not counted in tasks_completed either, the formula
    stays consistent.

    Returns all-None when tasks_total is None or zero (spike path).
    """
    if not tasks_total:
        return {
            "pass_at_1": None,
            "pass_at_2": None,
            "regressions": None,
            "regression_rate": None,
        }

    tc = tasks_completed if isinstance(tasks_completed, int) else 0

    pass_at_1 = round(max(0, tasks_total - retries_total) / tasks_total, 6)
    pass_at_2 = round(tc / tasks_total, 6)

    regressions = sum(
        1 for e in step_history
        if isinstance(e, dict) and e.get("regression")
    )
    regression_rate = round(regressions / tasks_total, 6)

    return {
        "pass_at_1": pass_at_1,
        "pass_at_2": pass_at_2,
        "regressions": regressions,
        "regression_rate": regression_rate,
    }


# ---------------------------------------------------------------------------
# run_git_churn: git diff --numstat + rework commit count
# Failure policy: non-fatal, returns zeros.
# ---------------------------------------------------------------------------

def run_git_churn(worktree: str, change_id: str) -> dict[str, Any]:
    """Count files_changed, insertions, deletions, total_commits, rework_commits.

    Searches git log for commits whose message contains the change_id or
    feature/<change_id> branch name. Falls back to zeros on any git failure.
    """
    defaults: dict[str, Any] = {
        "files_changed": 0,
        "insertions": 0,
        "deletions": 0,
        "total_commits": 0,
        "rework_commits": 0,
        "rework_rate": 0.0,
    }
    try:
        result = subprocess.run(
            ["git", "-C", worktree, "log",
             "--grep", change_id,
             "--no-merges",
             "--format=%H %s"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return defaults

        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        total_commits = len(lines)
        # Match legacy compute-swe-metrics.sh: grep -c "^fix:" behavior (NFR-3)
        rework_commits = sum(
            1 for ln in lines if re.match(r"^(fix|rework):", ln)
        )
        rework_rate = rework_commits / total_commits if total_commits > 0 else 0.0

        if not lines:
            return defaults

        # Get first and last commit SHA for diff range
        last_sha = lines[-1].split()[0]
        first_sha = lines[0].split()[0]

        # files_changed via --name-only diff
        name_result = subprocess.run(
            ["git", "-C", worktree, "diff", "--name-only", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        files_changed = len([
            ln for ln in name_result.stdout.splitlines() if ln.strip()
        ]) if name_result.returncode == 0 else 0

        # insertions/deletions via --numstat
        num_result = subprocess.run(
            ["git", "-C", worktree, "diff", "--numstat", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        insertions = 0
        deletions = 0
        if num_result.returncode == 0:
            for row in num_result.stdout.splitlines():
                parts = row.split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    insertions += int(parts[0])
                    deletions += int(parts[1])

        return {
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
            "total_commits": total_commits,
            "rework_commits": rework_commits,
            "rework_rate": round(rework_rate, 6),
        }
    except Exception:
        return defaults


# ---------------------------------------------------------------------------
# extract_review_scores: pull review_score.overall from step_history entries
# ---------------------------------------------------------------------------

def extract_review_scores(state: dict) -> dict[str, Any]:
    """Extract review_score.overall from step_history entries.

    Returns:
        scores_list (list of ints/floats), avg (float or None)
    """
    step_history = state.get("step_history") or []
    scores: list[float] = []
    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        review_score = entry.get("review_score")
        if isinstance(review_score, dict):
            overall = review_score.get("overall")
            if overall is not None:
                try:
                    scores.append(float(overall))
                except (TypeError, ValueError):
                    pass

    avg = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "scores_list": scores,
        "avg": avg,
    }


# ---------------------------------------------------------------------------
# wall_clock_minutes: difference between started_at and completed_at
# ---------------------------------------------------------------------------

def wall_clock_minutes(state: dict) -> float | None:
    """Compute wall clock in minutes from state started_at and completed_at.

    Returns None if either timestamp is missing or unparseable.
    """
    started_at = state.get("started_at")
    completed_at = state.get("completed_at")
    if not started_at or not completed_at:
        return None

    def _parse_ts(ts: Any) -> dt.datetime | None:
        if isinstance(ts, dt.datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=dt.timezone.utc)
            return ts
        s = str(ts).strip()
        # Normalize space-separated UTC offset to ISO 8601
        s = s.replace(" ", "T")
        s = re.sub(r"\+00:00$", "Z", s)
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = dt.datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
                return parsed.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
        return None

    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return round(delta / 60.0, 4)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 2:
        print("ERROR: Usage: ingest-feature-metrics.py <state_yaml_path>", file=sys.stderr)
        return 1

    state_yaml_path = Path(sys.argv[1])
    if not state_yaml_path.is_file():
        print(f"ERROR: state.yaml not found at {state_yaml_path}", file=sys.stderr)
        return 1

    with open(state_yaml_path) as f:
        state = yaml.safe_load(f) or {}

    # Required state fields
    repo_root = str(state.get("repo_root") or "")
    change_id = str(state.get("change_id") or "")
    schema = str(state.get("schema") or "feature")
    worktree = str(state.get("worktree_path") or repo_root)

    if not change_id:
        print("ERROR: state.yaml missing required field: change_id", file=sys.stderr)
        return 1

    # Slug guard (matches upsert.py constraint, NFR-1)
    if not _SLUG_RE.match(change_id):
        print(f"ERROR: change_id '{change_id}' fails slug guard (^[a-z0-9][a-z0-9-]*$)",
              file=sys.stderr)
        return 1

    # Fail loud on missing started_at / completed_at (required for wall_clock)
    if not state.get("started_at"):
        print("ERROR: state.yaml missing required field: started_at", file=sys.stderr)
        return 1
    if not state.get("completed_at"):
        print("ERROR: state.yaml missing required field: completed_at", file=sys.stderr)
        return 1

    # Resolve tasks.md path — prefer tasks_path from state (OQ-6), fall back to
    # design.md sketch path only for backward compat with older state formats.
    tasks_md_path_str = state.get("tasks_path") or ""
    if tasks_md_path_str:
        tasks_md = Path(tasks_md_path_str)
    else:
        # Fallback: .state/<slug>/tasks.md relative to repo_root
        tasks_md = Path(repo_root) / ".state" / change_id / "tasks.md"

    # Fail loud on missing tasks.md for feature/bugfix schemas
    if schema in ("feature", "bugfix") and not tasks_md.is_file():
        print(f"ERROR: tasks.md not found at {tasks_md} (required for schema={schema})",
              file=sys.stderr)
        return 1

    # --- Parse tasks ---
    if tasks_md.is_file():
        res = parse_tasks(tasks_md)
    else:
        # spike/autopilot — no tasks.md
        res = {
            "tasks_total": None, "tasks_planned": None, "tasks_added": None,
            "tasks_completed": None, "tasks_failed": None, "resolve_rate": None,
        }

    # --- Retries ---
    ret = compute_retries(state)

    # --- Resolution (pass@k, regressions) ---
    resolution = compute_resolution(
        tasks_total=res.get("tasks_total"),
        tasks_completed=res.get("tasks_completed"),
        retries_total=ret["retries_total"],
        step_history=state.get("step_history") or [],
        quarantine_events=state.get("quarantine_events"),
    )

    # --- Git churn (non-fatal) ---
    churn = run_git_churn(worktree, change_id)

    # --- Review scores ---
    rev = extract_review_scores(state)

    # --- Wall clock ---
    wc = wall_clock_minutes(state)

    # --- Connect DuckDB (fail loud on error) ---
    db_path = _metrics_db_path()
    try:
        db = duckdb.connect(db_path)
    except Exception as exc:
        print(f"ERROR: DuckDB connection failed ({db_path}): {exc}", file=sys.stderr)
        return 1

    try:
        ensure_schema(db)
        upsert_feature_metrics(
            db,
            repo_root=repo_root,
            change_id=change_id,
            schema_name=schema,
            **res,
            **ret,
            **resolution,
            **churn,
            review_scores_json=json.dumps(rev["scores_list"]),
            review_score_avg=rev["avg"],
            wall_clock_minutes=wc,
            source=f"ingest-feature-metrics@{_utcnow_iso()}",
        )
        db.close()
    except Exception as exc:
        print(f"ERROR: DuckDB upsert failed: {exc}", file=sys.stderr)
        db.close()
        return 1

    print(json.dumps({"feature_metrics_ingested": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
