#!/usr/bin/env python3
"""compute-prediction-accuracy.py — compute planning-accuracy metrics.

Port of the prose in config/steps/compute-prediction-accuracy.yaml.

Env inputs:
  STATE_YAML_PATH  — path to the workflow's state.yaml (required)
  REPO_ROOT        — repo root for git diff (required)

Outputs: {prediction_accuracy: {computed_at, predicted_tasks, actual_tasks,
  fix_task_count, rework_rate, task_delta, task_accuracy_pct,
  predicted_file_count, actual_file_count, file_delta, file_overlap_pct}}
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml


def count_tasks_from_yaml(tasks_yaml: Path) -> tuple[int, int]:
    """Return (predicted, actual) from tasks.yaml.

    ORC-65: tasks.yaml is the authoritative source. Fix tasks have ids like
    'fix-1', 'fix-2'; initial tasks are 'T-1', 'T-2', etc.
    predicted = initial task count; actual = all tasks.
    """
    if not tasks_yaml.is_file():
        return 0, 0
    try:
        data = yaml.safe_load(tasks_yaml.read_text()) or {}
    except Exception:
        return 0, 0
    tasks = data.get("tasks") or []
    actual = len(tasks)
    fix_count = sum(1 for t in tasks if str(t.get("id", "")).startswith("fix-"))
    predicted = actual - fix_count
    return max(predicted, 0) or actual, actual


def count_tasks(tasks_md: Path) -> tuple[int, int]:
    """Return (predicted, actual) from tasks.md (legacy fallback).

    Deprecated: ORC-65 replaced with count_tasks_from_yaml. Kept for backward
    compatibility with archived runs that have no tasks.yaml.
    """
    if not tasks_md.is_file():
        return 0, 0
    lines = tasks_md.read_text().splitlines()
    ids: list[str] = []
    for line in lines:
        m = re.match(r'^- \[[ x]\] (T-[^\s:]+)', line)
        if m:
            ids.append(m.group(1))
    actual = len(ids)
    # Pure sequential T-1, T-2, ... → predicted. Everything else is a fix.
    predicted = 0
    for i, tid in enumerate(ids, start=1):
        if tid == f"T-{i}":
            predicted += 1
        else:
            break  # stop at first non-sequential
    # If all tasks are sequential, predicted == actual.
    return predicted or actual, actual


def extract_predicted_files(design_md: Path) -> list[str] | None:
    if not design_md.is_file():
        return None
    text = design_md.read_text()
    # Very loose heuristic — find a markdown pipe table that has a column
    # named 'File' or a section with 'Files:' / 'Affected Files'.
    file_paths: list[str] = []
    for m in re.finditer(r'`([^\s`]+\.[a-zA-Z]+)`', text):
        file_paths.append(m.group(1))
    return file_paths or None


def git_diff_files(repo_root: str) -> list[str] | None:
    for base in ("main", "origin/main"):
        try:
            r = subprocess.run(
                ["git", "-C", repo_root, "diff", "--name-only", f"{base}...HEAD"],
                capture_output=True, text=True, check=True,
            )
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:
            continue
    return None


def main() -> int:
    state_path = os.environ.get("STATE_YAML_PATH")
    repo_root = os.environ.get("REPO_ROOT") or os.getcwd()
    if not state_path or not Path(state_path).is_file():
        print(json.dumps({"error": "STATE_YAML_PATH missing or not a file"}))
        return 3

    # Sibling lookup: tasks.yaml and design.md live alongside state.yaml in
    # spec/changes/<slug>/ (the canonical artifact dir, ORC-36). Do NOT diverge.
    state_dir = Path(state_path).parent
    tasks_yaml = state_dir / "tasks.yaml"
    tasks_md = state_dir / "tasks.md"  # legacy fallback
    design_md = state_dir / "design.md"

    # ORC-65: prefer tasks.yaml; fall back to tasks.md for archived runs.
    if tasks_yaml.is_file():
        predicted_tasks, actual_tasks = count_tasks_from_yaml(tasks_yaml)
    else:
        predicted_tasks, actual_tasks = count_tasks(tasks_md)
    fix_task_count = max(actual_tasks - predicted_tasks, 0)
    rework_rate = (fix_task_count / actual_tasks) if actual_tasks > 0 else 0.0
    task_delta = actual_tasks - predicted_tasks
    task_accuracy_pct = (
        round((predicted_tasks / actual_tasks) * 100, 1)
        if actual_tasks > 0 else 100.0
    )

    predicted_files = extract_predicted_files(design_md)
    actual_files = git_diff_files(repo_root)

    if predicted_files is not None and actual_files is not None:
        pf_set = set(predicted_files)
        af_set = set(actual_files)
        both = pf_set & af_set
        predicted_file_count = len(pf_set)
        actual_file_count = len(af_set)
        file_delta = actual_file_count - predicted_file_count
        denom = max(predicted_file_count, actual_file_count) or 1
        file_overlap_pct = round((len(both) / denom) * 100, 1)
    else:
        predicted_file_count = actual_file_count = file_delta = file_overlap_pct = None

    result = {"prediction_accuracy": {
        "computed_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "predicted_tasks": predicted_tasks,
        "actual_tasks": actual_tasks,
        "fix_task_count": fix_task_count,
        "rework_rate": round(rework_rate, 3),
        "task_delta": task_delta,
        "task_accuracy_pct": task_accuracy_pct,
        "predicted_file_count": predicted_file_count,
        "actual_file_count": actual_file_count,
        "file_delta": file_delta,
        "file_overlap_pct": file_overlap_pct,
    }}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
