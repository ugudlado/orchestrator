#!/usr/bin/env python3
"""DuckDB inputs for workflow-learner (operator workflow step)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_query(script: Path, repo_root: str, *args: str) -> str:
    extra: list[str] = []
    if args and args[0] == "retry-hotspots":
        extra = ["--fleet"]
    else:
        extra = ["--repo", repo_root]
    env = {**os.environ, "REPO_ROOT": repo_root}
    proc = subprocess.run(
        ["bash", str(script), *extra, *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return (proc.stdout or "").strip()


def main() -> int:
    repo_root = os.environ.get("REPO_ROOT", "")
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    if not repo_root or not scripts_dir:
        print("error: REPO_ROOT and ORCHESTRATOR_SCRIPTS_DIR required", file=sys.stderr)
        return 1

    metrics_sh = Path(scripts_dir) / "metrics" / "metrics-query.sh"
    if not metrics_sh.is_file():
        print(json.dumps({"learn_metrics": {"status": "unavailable", "reason": "metrics-query.sh missing"}}))
        return 0

    scope = os.environ.get("LEARN_SCOPE", "all")
    state = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    retry_limit = os.environ.get("LEARN_RETRY_HOTSPOTS_LIMIT", "10")
    recent_limit = os.environ.get("LEARN_RECENT_FEATURES_LIMIT", "10")
    quality_limit = os.environ.get("LEARN_QUALITY_TREND_LIMIT", "5")

    print(
        json.dumps(
            {
                "learn_metrics": {
                    "scope": scope,
                    "state_yaml_path": state,
                    "retry_hotspots_csv": _run_query(
                        metrics_sh, repo_root, "retry-hotspots", "--limit", retry_limit
                    ),
                    "cycle_count_csv": _run_query(metrics_sh, repo_root, "cycle-count"),
                    "recent_features_csv": _run_query(
                        metrics_sh, repo_root, "recent-features", "--limit", recent_limit
                    ),
                    "quality_trend_csv": _run_query(
                        metrics_sh, repo_root, "quality-trend", "--limit", quality_limit
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
