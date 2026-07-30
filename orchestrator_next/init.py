"""`orchestrator init` — scaffold spec/project.yaml for a new repo (T2/T3 of
distribution improvements). Mechanical, no flags in v1: writes the bundled
template to <repo_root>/spec/project.yaml, refuses to overwrite an existing
one, and prints next steps.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from orchestrator_next.paths import bundled_config_root


def _repo_root() -> Path:
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    if repo_root:
        return Path(repo_root).expanduser().resolve()
    return Path.cwd()


def main(argv: list[str]) -> int:
    ap_help = "--help" in argv or "-h" in argv
    if ap_help:
        print("Usage: orchestrator init\n"
              "  Writes spec/project.yaml from the bundled template. Refuses to\n"
              "  overwrite an existing spec/project.yaml.")
        return 0

    repo_root = _repo_root()
    target = repo_root / "spec" / "project.yaml"

    if target.exists():
        print(f"spec/project.yaml already exists at {target} — leaving it as-is.")
        return 0

    template_path = bundled_config_root() / "templates" / "project.yaml"
    try:
        content = template_path.read_text()
    except OSError as exc:
        print(f"error: could not read template at {template_path} — {exc}", file=sys.stderr)
        return 3

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)

    print(f"wrote {target}")
    print("Next steps:")
    print("  orchestrator doctor")
    print("  orchestrator run <id> --schema <s>")
    return 0
