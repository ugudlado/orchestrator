#!/usr/bin/env python3
"""Wrap metrics/estimate-cost.sh and emit route_preview JSON."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def _unavailable(reason: str, **extra) -> None:
    payload = {"route_preview": {"status": "estimate_unavailable", "reason": reason, **extra}}
    print(json.dumps(payload))


def _parse_estimator_output(path: Path) -> None:
    txt = path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(txt)
    except yaml.YAMLError as exc:
        parsed = {"status": "estimate_unavailable", "reason": f"parse error: {exc}"}
    if isinstance(parsed, dict) and "route_preview" in parsed:
        print(json.dumps({"route_preview": parsed["route_preview"]}))
    else:
        print(
            json.dumps(
                {
                    "route_preview": parsed
                    if parsed
                    else {"status": "estimate_unavailable", "reason": "empty output"}
                }
            )
        )


def _arg_dir() -> str:
    workflow_dir = os.environ.get("ORCHESTRATOR_WORKFLOW_DIR", "")
    change_id = os.environ.get("ORCHESTRATOR_CHANGE_ID", "")
    repo_root = os.environ.get("REPO_ROOT", "")
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    if change_id and repo_root:
        resolve = Path(scripts_dir) / "metrics" / "resolve-state-yaml.sh"
        if resolve.is_file():
            proc = subprocess.run(
                ["bash", str(resolve), change_id, repo_root],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return str(Path(proc.stdout.strip()).parent)
    return workflow_dir


def main() -> int:
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    if not scripts_dir:
        print("error: ORCHESTRATOR_SCRIPTS_DIR required", file=sys.stderr)
        return 1

    estimator = Path(scripts_dir) / "metrics" / "estimate-cost.sh"
    if not estimator.is_file():
        _unavailable("estimator not found")
        return 0

    workflow_dir = os.environ.get("ORCHESTRATOR_WORKFLOW_DIR", "")
    if not workflow_dir or not Path(workflow_dir).is_dir():
        _unavailable("workflow dir missing")
        return 0

    arg_dir = _arg_dir()
    with tempfile.NamedTemporaryFile(prefix="preview-route-out-", delete=False) as out_f:
        out_path = Path(out_f.name)
    err_path = Path(tempfile.mktemp(prefix="preview-route-err-"))
    try:
        proc = subprocess.run(
            ["bash", str(estimator), arg_dir],
            stdout=out_path.open("w", encoding="utf-8"),
            stderr=err_path.open("w", encoding="utf-8"),
        )
        if proc.returncode != 0 or not out_path.stat().st_size:
            reason = err_path.read_text(encoding="utf-8")[:200].replace("\n", " ")
            _unavailable(reason, exit_code=proc.returncode)
        else:
            _parse_estimator_output(out_path)
    finally:
        out_path.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
