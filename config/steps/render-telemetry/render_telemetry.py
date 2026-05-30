#!/usr/bin/env python3
"""DuckDB metrics dashboard (operator workflow step)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_query(script: Path, repo_root: str, fleet: bool, *args: str) -> str:
    extra: list[str] = [] if fleet else ["--repo", repo_root]
    env = {**os.environ, "REPO_ROOT": repo_root}
    cmd = ["bash", str(script), *extra, *args]
    if fleet:
        cmd.append("--fleet")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return (proc.stdout or "").strip()


def main() -> int:
    repo_root = os.environ.get("REPO_ROOT", "")
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    if not repo_root or not scripts_dir:
        print("error: REPO_ROOT and ORCHESTRATOR_SCRIPTS_DIR required", file=sys.stderr)
        return 1

    metrics_sh = Path(scripts_dir) / "metrics" / "metrics-query.sh"
    if not metrics_sh.is_file():
        print("No telemetry data (metrics-query.sh not found).", file=sys.stderr)
        return 1

    scope = os.environ.get("TELEMETRY_SCOPE", "recent")
    fleet = os.environ.get("TELEMETRY_FLEET", "") == "1"
    features_limit = os.environ.get("TELEMETRY_FEATURES_LIMIT", "5")
    trend_limit = os.environ.get("TELEMETRY_TREND_LIMIT", "10")
    hotspots_limit = os.environ.get("TELEMETRY_HOTSPOTS_LIMIT", "10")

    repo_name = Path(repo_root).name
    fleet_label = " (fleet)" if fleet else ""
    limit_args = ["--limit", features_limit] if scope == "recent" else []

    print("═══════════════════════════════════════════════════")
    print(f"  WORKFLOW TELEMETRY — {repo_name}")
    print(f"  Scope: {scope}{fleet_label}")
    print("═══════════════════════════════════════════════════")
    print()

    def section(title: str, body: str) -> None:
        print(title)
        print("─────────────────────────────────────────────────")
        print(body)
        print()

    recent = _run_query(metrics_sh, repo_root, fleet, "recent-features", *limit_args)
    if not recent:
        print("No archived metrics in DuckDB for this scope.")
        print(
            "Complete a feature workflow or run "
            "orchestrator_next/scripts/metrics/register-repo.sh to backfill archives."
        )
        print()
        return 0

    section("RECENT FEATURES", recent)

    cost = _run_query(metrics_sh, repo_root, fleet, "cost-trend", "--limit", trend_limit)
    if cost:
        section("COST TREND", cost)

    quality = _run_query(metrics_sh, repo_root, fleet, "quality-trend", "--limit", trend_limit)
    if quality:
        section("QUALITY TREND", quality)

    retries = _run_query(metrics_sh, repo_root, fleet, "retry-hotspots", "--limit", hotspots_limit)
    if retries:
        section("RETRY HOTSPOTS", retries)

    steps = _run_query(
        metrics_sh, repo_root, fleet, "step-cost-hotspots", "--limit", hotspots_limit
    )
    if steps:
        section("STEP COST HOTSPOTS", steps)

    print("═══════════════════════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
