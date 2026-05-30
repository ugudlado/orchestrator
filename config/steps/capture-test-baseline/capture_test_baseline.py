#!/usr/bin/env python3
"""Run project test command and emit baseline JSON on stdout."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _test_command(project_yaml: Path) -> str:
    try:
        data = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    except OSError:
        return ""
    vc = data.get("verify_commands") or {}
    if isinstance(vc, dict):
        return str(vc.get("test", "") or "")
    if isinstance(vc, list) and vc and isinstance(vc[0], str):
        return vc[0]
    return ""


def _parse_output(text: str, *, captured_at: str, test_command: str, exit_code: int) -> dict:
    patterns = [
        (r"(\d+)\s+passed", r"(\d+)\s+failed", r"(\d+)\s+skipped"),
        (
            r"Tests:.*?(\d+)\s+passed",
            r"Tests:.*?(\d+)\s+failed",
            r"Tests:.*?(\d+)\s+skipped",
        ),
        (r"test result:.*?(\d+)\s+passed", r"test result:.*?(\d+)\s+failed", None),
    ]
    for passed_p, failed_p, skipped_p in patterns:
        mp = re.search(passed_p, text)
        if not mp:
            continue
        passing = int(mp.group(1))
        mf = re.search(failed_p, text) if failed_p else None
        failing = int(mf.group(1)) if mf else 0
        ms = re.search(skipped_p, text) if skipped_p else None
        skipped = int(ms.group(1)) if ms else 0
        return {
            "baseline": {
                "captured_at": captured_at,
                "test_command": test_command,
                "passing": passing,
                "failing": failing,
                "skipped": skipped,
                "total": passing + failing + skipped,
                "exit_code": exit_code,
            }
        }
    tail = "\n".join(text.splitlines()[-20:])
    return {
        "baseline": {
            "skipped": True,
            "reason": "unparseable",
            "raw_tail": tail,
            "exit_code": exit_code,
        }
    }


def main() -> int:
    repo_root = os.environ.get("REPO_ROOT", "")
    if not repo_root:
        print("error: REPO_ROOT required", file=sys.stderr)
        return 1

    project_yaml = Path(repo_root) / "spec" / "project.yaml"
    if not project_yaml.is_file():
        print(json.dumps({"baseline": {"skipped": True, "reason": "spec/project.yaml not found"}}))
        return 0

    test_cmd = _test_command(project_yaml)
    if not test_cmd:
        print(json.dumps({"baseline": {"skipped": True, "reason": "no test command in project.yaml"}}))
        return 0

    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    proc = subprocess.run(
        ["bash", "-c", test_cmd],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    result = _parse_output(
        proc.stdout + proc.stderr,
        captured_at=captured_at,
        test_command=test_cmd,
        exit_code=proc.returncode,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
