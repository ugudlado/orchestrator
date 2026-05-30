#!/usr/bin/env python3
"""Emit feature cost markdown + completion JSON before archive."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from state_yaml import change_id, cost_summary_relpath, load  # noqa: E402


def main() -> int:
    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    if not state_path or not scripts_dir:
        print("error: ORCHESTRATOR_STATE_YAML_PATH and ORCHESTRATOR_SCRIPTS_DIR required", file=sys.stderr)
        return 1
    path = Path(state_path)
    if not path.is_file():
        print("cost-report: state.yaml not found", file=sys.stderr)
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing state.yaml"}}))
        return 1

    cost_sh = Path(scripts_dir) / "metrics" / "cost-report.sh"
    if not cost_sh.is_file():
        print("cost-report: metrics/cost-report.sh not found", file=sys.stderr)
        print(json.dumps({"status": "failed", "evidence": {"summary": "cost-report.sh missing"}}))
        return 1

    cid = change_id(load(path))
    if not cid:
        print("cost-report: change_id missing in state.yaml", file=sys.stderr)
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing change_id"}}))
        return 1

    change_dir = path.parent
    summary_path = change_dir / "cost-summary.md"
    err_path = change_dir / ".cost-report.err"
    proc = subprocess.run(
        ["bash", str(cost_sh), "--change-id", cid],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        err_path.write_text(proc.stderr or "", encoding="utf-8")
        print("cost-report: full report failed (see .cost-report.err)", file=sys.stderr)
        if err_path.is_file():
            sys.stderr.write(err_path.read_text(encoding="utf-8"))
        print(json.dumps({"status": "failed", "evidence": {"summary": "cost-report.sh exited non-zero"}}))
        return 1
    summary_path.write_text(proc.stdout or "", encoding="utf-8")
    err_path.unlink(missing_ok=True)

    tail_proc = subprocess.run(
        ["bash", str(cost_sh), "--change-id", cid, "--tail"],
        capture_output=True,
        text=True,
    )
    tail_line = (tail_proc.stdout or "").strip()
    if tail_line:
        print(f"cost-report: {tail_line}", file=sys.stderr)

    rel_path = cost_summary_relpath(path, summary_path)
    print(
        json.dumps(
            {
                "status": "completed",
                "outputs": {
                    "tail_summary": tail_line,
                    "cost_summary_path": rel_path,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
