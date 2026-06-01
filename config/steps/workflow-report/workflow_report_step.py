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


def _render_report(step_history: list, issues: list) -> None:
    """Print per-step duration/tokens/cost table to stderr."""
    if not step_history:
        return

    sys.stderr.write("\n## Workflow step report\n\n")
    sys.stderr.write(f"{'Step':<35} {'Status':<12} {'Duration':>10} {'Tokens':>10} {'Cost':>10}\n")
    sys.stderr.write(f"{'-'*35} {'-'*12} {'-'*10} {'-'*10} {'-'*10}\n")

    total_tokens = 0
    total_cost = 0.0
    total_ms = 0

    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        step_id = entry.get("step_id") or "?"
        status = entry.get("status") or "?"
        usage = entry.get("usage") or {}

        duration_ms = usage.get("duration_ms") or 0
        tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        cost = usage.get("cost_usd") or 0.0

        total_ms += duration_ms
        total_tokens += tokens
        total_cost += cost

        dur_str = f"{duration_ms / 1000:.1f}s" if duration_ms else "—"
        tok_str = f"{tokens:,}" if tokens else "—"
        cost_str = f"${cost:.4f}" if cost else "—"

        sys.stderr.write(f"{step_id:<35} {status:<12} {dur_str:>10} {tok_str:>10} {cost_str:>10}\n")

    sys.stderr.write(f"\n{'TOTAL':<35} {'':12} {total_ms/1000:.1f}s {total_tokens:>10,} ${total_cost:>9.4f}\n")

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

    step_history = state.get("step_history") or []
    issues = state.get("workflow_issues") or []

    _render_report(step_history, issues)

    outputs: dict = {"steps_reported": len(step_history)}
    if issues:
        outputs["workflow_issues_count"] = len(issues)

    print(json.dumps({"status": "completed", "outputs": outputs}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
