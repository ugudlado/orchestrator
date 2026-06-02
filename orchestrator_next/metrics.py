"""Feature-metrics computation extracted from record.py (ORC-74)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any

_PHASE_REVIEW_VERDICTS = frozenset({"pass", "needs_work", "incomplete_phase"})


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_task_counts(tasks_yaml_path: "Path | None") -> dict:
    """Derive task counts from tasks.yaml status fields.

    Reads tasks.yaml directly — the single authority on task completion since
    implement-tasks writes status: completed per task after each commit.
    Fix tasks have ids starting with 'fix-'.

    Returns a dict with:
        tasks_total     — total number of tasks in tasks.yaml
        tasks_planned   — tasks whose id does not start with 'fix-'
        tasks_added     — tasks whose id starts with 'fix-' (rework additions)
        tasks_completed — tasks with status: completed
        tasks_failed    — tasks neither completed nor skipped
        resolve_rate    — tasks_completed / tasks_total (0.0 when total is 0)

    Returns None values when tasks.yaml is absent or has no tasks (spike path).
    """
    import yaml as _yaml

    _null = {
        "tasks_total": None,
        "tasks_planned": None,
        "tasks_added": None,
        "tasks_completed": None,
        "tasks_failed": None,
        "resolve_rate": None,
    }

    if tasks_yaml_path is None or not tasks_yaml_path.is_file():
        return _null

    try:
        doc = _yaml.safe_load(tasks_yaml_path.read_text(encoding="utf-8")) or {}
    except (_yaml.YAMLError, OSError):
        return _null

    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        return _null

    total = len(tasks)
    fix_count = sum(1 for t in tasks if str(t.get("id", "")).startswith("fix-"))
    planned = total - fix_count
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    failed = total - completed
    resolve_rate = completed / total if total > 0 else 0.0

    return {
        "tasks_total": total,
        "tasks_planned": planned,
        "tasks_added": fix_count,
        "tasks_completed": completed,
        "tasks_failed": failed,
        "resolve_rate": round(resolve_rate, 6),
    }


def compute_retries(state: dict) -> dict:
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


def compute_resolution(
    tasks_total,
    tasks_completed,
    retries_total: int,
    step_history: list,
    quarantine_events,
) -> dict:
    """Derive pass_at_1, pass_at_2, regressions, regression_rate.

    Approximation note: state.yaml retries are keyed by step_id (e.g.
    "run-phase-review"), not by task_id — so per-task attempt granularity
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


def run_git_churn(worktree: str, change_id: str) -> dict:
    """Count files_changed, insertions, deletions, total_commits, rework_commits.

    Searches git log for commits whose message contains the change_id or
    feature/<change_id> branch name. Falls back to zeros on any git failure.
    """
    import subprocess as _subprocess
    defaults: dict = {
        "files_changed": 0,
        "insertions": 0,
        "deletions": 0,
        "total_commits": 0,
        "rework_commits": 0,
        "rework_rate": 0.0,
    }
    try:
        result = _subprocess.run(
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
        name_result = _subprocess.run(
            ["git", "-C", worktree, "diff", "--name-only", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        files_changed = len([
            ln for ln in name_result.stdout.splitlines() if ln.strip()
        ]) if name_result.returncode == 0 else 0

        # insertions/deletions via --numstat
        num_result = _subprocess.run(
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


def _phase_review_verdict(entry: dict) -> str | None:
    """Read verdict from step_history evidence.outputs.phase_review_report.

    Payload-time validation uses top-level ``outputs`` (_validate_phase_review_output);
    record nests those under ``evidence.outputs`` when appending step_history.
    """
    evidence = entry.get("evidence")
    if not isinstance(evidence, dict):
        return None
    outputs = evidence.get("outputs")
    if not isinstance(outputs, dict):
        return None
    report = outputs.get("phase_review_report")
    if isinstance(report, dict):
        verdict = report.get("verdict")
        return verdict if isinstance(verdict, str) else None
    return None


def extract_review_scores(state: dict) -> dict:
    """Extract review_score.overall from step_history entries.

    Only includes passing reviews (verdict ``pass``) and legacy entries that
    predate the verdict field. ``needs_work`` and ``incomplete_phase`` attempts
    are excluded so ``review_score_avg`` reflects achieved quality, not failed
    review rounds.

    Returns:
        scores_list (list of ints/floats), avg (float or None)
    """
    step_history = state.get("step_history") or []
    scores: list = []
    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        verdict = _phase_review_verdict(entry)
        if verdict is not None and verdict != "pass":
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


def wall_clock_minutes(state: dict):
    """Compute wall clock in minutes from state started_at and completed_at.

    Returns None if either timestamp is missing or unparseable.
    """
    started_at = state.get("started_at")
    completed_at = state.get("completed_at")
    if not started_at or not completed_at:
        return None

    def _parse_ts(ts):
        if isinstance(ts, _dt.datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=_dt.timezone.utc)
            return ts
        s = str(ts).strip()
        # Normalize space-separated UTC offset to ISO 8601
        s = s.replace(" ", "T")
        s = re.sub(r"\+00:00$", "Z", s)
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = _dt.datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
                return parsed.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                continue
        return None

    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return round(delta / 60.0, 4)


def _resolve_workflow_artifact_path(state_raw: dict[str, Any], filename: str) -> Path | None:
    """Unified resolver for workflow artifact files (tasks.md, design.md, etc.).

    Resolution order:
      1. If filename == "tasks.md" and state_raw has an explicit ``tasks_path``
         override, return that path immediately.
      2. If ``worktree_path`` is set AND that directory exists on disk AND the
         candidate file itself exists, return
         ``<worktree_path>/spec/changes/<change_id>/<filename>``; otherwise fall
         through to priority 3.
      3. Else if ``repo_root`` is set, return
         ``<repo_root>/spec/changes/<change_id>/<filename>``.
      4. Return None when no candidate can be constructed at all.
    """
    # 1. Explicit tasks_path override (tasks.md only).
    if filename == "tasks.md":
        raw_path = state_raw.get("tasks_path")
        if isinstance(raw_path, str) and raw_path:
            return Path(os.path.expanduser(raw_path))

    change_id = state_raw.get("change_id")
    if not (isinstance(change_id, str) and change_id):
        return None

    # 2. Worktree path — only when the directory actually exists.
    worktree_path = state_raw.get("worktree_path")
    if isinstance(worktree_path, str) and worktree_path:
        wt = Path(os.path.expanduser(worktree_path))
        if wt.is_dir():
            candidate = wt / "spec" / "changes" / change_id / filename
            if candidate.is_file():
                return candidate

    # 3. Fall back to repo_root.
    repo_root = state_raw.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        return Path(os.path.expanduser(repo_root)) / "spec" / "changes" / change_id / filename

    return None


def _resolve_feature_metrics_tasks_path(state: dict) -> Path:
    """Thin wrapper: resolve tasks.md path for feature_metrics computation."""
    return _resolve_workflow_artifact_path(state, "tasks.md") or Path("")


def _resolve_feature_metrics(state: dict, change_id: str) -> dict:
    """Compute feature metrics from state and tasks.yaml. Pure — no DB writes.

    Raises RuntimeError if started_at/completed_at missing on feature/bugfix.
    """
    schema = str(state.get("schema") or "feature")
    worktree = str(state.get("worktree_path") or state.get("repo_root") or "")

    if schema in ("feature", "bugfix"):
        if not state.get("started_at") or not state.get("completed_at"):
            raise RuntimeError(
                f"_resolve_feature_metrics: state missing started_at/completed_at "
                f"for schema={schema}"
            )

    tasks_yaml = _resolve_workflow_artifact_path(state, "tasks.yaml")
    task_counts = compute_task_counts(tasks_yaml)

    retries = compute_retries(state)
    resolution = compute_resolution(
        tasks_total=task_counts.get("tasks_total"),
        tasks_completed=task_counts.get("tasks_completed"),
        retries_total=retries["retries_total"],
        step_history=state.get("step_history") or [],
        quarantine_events=state.get("quarantine_events"),
    )
    churn = run_git_churn(worktree, change_id)
    reviews = extract_review_scores(state)
    wc = wall_clock_minutes(state)

    return {
        "schema_name": schema,
        **task_counts,
        **retries,
        **resolution,
        **churn,
        "review_scores_json": json.dumps(reviews["scores_list"]),
        "review_score_avg": reviews["avg"],
        "wall_clock_minutes": wc,
        "source": f"done@{_utcnow_iso()}",
    }
