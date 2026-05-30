#!/usr/bin/env python3
"""Append task-nodes from tasks.yaml via orchestrator expand-plan."""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    state_path = os.environ.get("STATE_YAML_PATH", "")
    repo_root = os.environ.get("REPO_ROOT", "")
    if not state_path or not repo_root:
        print("error: STATE_YAML_PATH and REPO_ROOT required", file=sys.stderr)
        return 1
    orch = os.path.join(repo_root, "bin", "orchestrator")
    proc = subprocess.run([sys.executable, orch, "expand-plan", state_path], check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
