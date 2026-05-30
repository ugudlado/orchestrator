#!/usr/bin/env python3
"""Archive step for workflow completion (delegates to archive-completed-change)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from json_lines import extract_record  # noqa: E402


def main() -> int:
    repo_root = os.environ.get("REPO_ROOT", "")
    change_id = os.environ.get("CHANGE_ID", "")
    archive_path = os.environ.get("ARCHIVE_PATH", "")
    orch_home = os.environ.get("ORCHESTRATOR_HOME", "")
    for name, val in (
        ("REPO_ROOT", repo_root),
        ("CHANGE_ID", change_id),
        ("ARCHIVE_PATH", archive_path),
        ("ORCHESTRATOR_HOME", orch_home),
    ):
        if not val:
            print(f"error: {name} required", file=sys.stderr)
            return 1

    archive_script = Path(orch_home) / "config/steps/archive-completed-change/script.sh"
    proc = subprocess.run(
        ["bash", str(archive_script)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    merge_record = {
        "skipped": True,
        "reason": "merge deferred to orchestrator complete (after archive)",
    }
    worktree_record = {
        "skipped": True,
        "reason": "worktree removal deferred to orchestrator complete (after merge)",
    }
    archive_record = extract_record(proc.stdout, "archive_record")

    if proc.returncode != 0:
        if proc.stdout:
            sys.stderr.write(proc.stdout)
        print(
            json.dumps(
                {
                    "completion_record": {
                        "merge_record": merge_record,
                        "archive_record": archive_record,
                    }
                }
            )
        )
        return proc.returncode

    print(
        json.dumps(
            {
                "completion_record": {
                    "merge_record": merge_record,
                    "archive_record": archive_record,
                    "worktree_record": worktree_record,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
