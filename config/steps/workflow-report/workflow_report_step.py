#!/usr/bin/env python3
"""Workflow report step — duration, tokens, and cost per step from state.yaml."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from state_yaml import change_id as _change_id, load as _load_state  # noqa: E402


def _resolve_state(state_path: str, repo_root: str) -> tuple[Path, dict] | tuple[None, None]:
    import glob
    path = Path(state_path)
    if path.is_file():
        return path, _load_state(path)
    cid_hint = path.parent.name
    pattern = os.path.join(repo_root, "spec", "changes", "archive", f"*{cid_hint}", "state.yaml")
    for p in sorted(glob.glob(pattern)):
        candidate = Path(p)
        if candidate.is_file():
            return candidate, _load_state(candidate)
    return None, None


def _collect_all_states(primary_path: Path, primary_state: dict, repo_root: str) -> list[dict]:
    """Collect step_history from all state files for this change_id (feature + complete runs)."""
    import glob
    cid = _change_id(primary_state)
    if not cid:
        return [primary_state]

    # Gather all state files from the .orchestrator/<cid>/ dir (siblings of primary)
    state_dir = primary_path.parent
    sibling_files = sorted(state_dir.glob("*_state.yaml"))

    # Also check archive dir for any archived state
    archive_pattern = os.path.join(repo_root, "spec", "changes", "archive", f"*{cid}", "state.yaml")
    archive_files = [Path(p) for p in sorted(glob.glob(archive_pattern))]

    seen = set()
    states = []
    for f in sibling_files + archive_files:
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        s = _load_state(f)
        if _change_id(s) == cid:
            states.append(s)

    return states if states else [primary_state]


def _render_report(step_history: list, issues: list) -> dict:
    """Print per-step duration/tokens/cost table to stderr; return the same
    figures as a plain dict for structured (JSON) output."""
    if not step_history:
        return {"steps": [], "totals": {"duration_ms": 0, "tokens": 0, "cost_usd": 0.0}}

    # Collapse entries by step_id: accumulate tokens/cost across all attempts,
    # track final status and total attempt count.
    from collections import OrderedDict
    rows: OrderedDict = OrderedDict()
    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        step_id = entry.get("step_id") or "?"
        status = entry.get("status") or "?"
        attempt = entry.get("attempt") or 1
        usage = entry.get("usage") or {}
        tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        cost = usage.get("cost_usd") or 0.0
        duration_ms = usage.get("duration_ms") or 0
        if step_id not in rows:
            rows[step_id] = {"status": status, "attempts": attempt, "tokens": tokens, "cost": cost, "duration_ms": duration_ms}
        else:
            rows[step_id]["status"] = status  # last status wins
            rows[step_id]["attempts"] = max(rows[step_id]["attempts"], attempt)
            rows[step_id]["tokens"] += tokens
            rows[step_id]["cost"] += cost
            rows[step_id]["duration_ms"] += duration_ms

    sys.stderr.write("\n## Workflow step report\n\n")
    sys.stderr.write(f"{'Step':<35} {'Status':<12} {'Attempts':>8} {'Duration':>10} {'Tokens':>10} {'Cost':>10}\n")
    sys.stderr.write(f"{'-'*35} {'-'*12} {'-'*8} {'-'*10} {'-'*10} {'-'*10}\n")

    total_tokens = 0
    total_cost = 0.0
    total_ms = 0

    for step_id, r in rows.items():
        attempts = r["attempts"]
        duration_ms = r["duration_ms"]
        tokens = r["tokens"]
        cost = r["cost"]

        total_ms += duration_ms
        total_tokens += tokens
        total_cost += cost

        att_str = f"{attempts} ✗" if attempts > 1 else "1"
        dur_str = f"{duration_ms / 1000:.1f}s" if duration_ms else "—"
        tok_str = f"{tokens:,}" if tokens else "—"
        cost_str = f"${cost:.4f}" if cost else "—"

        sys.stderr.write(f"{step_id:<35} {r['status']:<12} {att_str:>8} {dur_str:>10} {tok_str:>10} {cost_str:>10}\n")

    sys.stderr.write(f"\n{'TOTAL':<35} {'':12} {'':>8} {total_ms/1000:.1f}s {total_tokens:>10,} ${total_cost:>9.4f}\n")

    if issues:
        sys.stderr.write(f"\n## Workflow issues ({len(issues)})\n\n")
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

    return {
        "steps": [
            {"step_id": step_id, "status": r["status"], "attempts": r["attempts"],
             "duration_ms": r["duration_ms"], "tokens": r["tokens"], "cost_usd": round(r["cost"], 6)}
            for step_id, r in rows.items()
        ],
        "totals": {"duration_ms": total_ms, "tokens": total_tokens, "cost_usd": round(total_cost, 6)},
    }


def main() -> int:
    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    repo_root = os.environ.get("REPO_ROOT", "")

    if not state_path:
        sys.stderr.write("error: ORCHESTRATOR_STATE_YAML_PATH required\n")
        return 1

    path, state = _resolve_state(state_path, repo_root)
    if path is None:
        sys.stderr.write(f"workflow-report: state.yaml not found at {state_path}\n")
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing state.yaml"}}))
        return 1

    cid = _change_id(state)
    if not cid:
        sys.stderr.write("workflow-report: change_id missing in state.yaml\n")
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing change_id"}}))
        return 1

    all_states = _collect_all_states(path, state, repo_root)
    step_history: list = []
    issues: list = []
    schemas_run: list = []
    for s in all_states:
        step_history.extend(s.get("step_history") or [])
        issues.extend(s.get("workflow_issues") or [])
        schema = s.get("schema")
        if schema and schema not in schemas_run:
            schemas_run.append(schema)

    report = _render_report(step_history, issues)

    # workflow_report is structured for future ingestion (no DB — console only,
    # per ORC decision to drop metrics.duckdb). change_id + schemas_run is the
    # join key across the separate design/implement/review runs for one ticket.
    outputs: dict = {
        "steps_reported": len(step_history),
        "workflow_report": {"change_id": cid, "schemas_run": schemas_run, **report},
    }
    if issues:
        outputs["workflow_issues_count"] = len(issues)

    print(json.dumps({"status": "completed", "outputs": outputs}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
