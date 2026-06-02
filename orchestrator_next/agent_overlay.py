"""Repo-scoped agent overlay text for headless dispatch (ORC-97)."""
from __future__ import annotations

import sys
from pathlib import Path

_OVERLAY_SEPARATOR = (
    "\n\n---\n"
    "# Repo-scoped agent overlay (.orchestrator/agents/)\n"
    "---\n\n"
)


def overlay_text(repo_root: str, agent_name: str) -> str:
    """Return overlay markdown for *agent_name*, or empty string when absent/empty."""
    path = Path(repo_root) / ".orchestrator" / "agents" / f"{agent_name}.md"
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if not content.strip():
        return ""
    return f"{_OVERLAY_SEPARATOR}{content.rstrip()}\n"


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python3 -m orchestrator_next.agent_overlay <repo_root> <agent>", file=sys.stderr)
        raise SystemExit(2)
    sys.stdout.write(overlay_text(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
