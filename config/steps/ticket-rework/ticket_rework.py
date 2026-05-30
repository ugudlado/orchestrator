#!/usr/bin/env python3
"""QA failed: move ticket back to In Progress (branch unchanged)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from state_yaml import load, ticket_rework_fields  # noqa: E402


def main() -> int:
    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    repo_root = os.environ.get("REPO_ROOT", "")
    scripts_dir = os.environ.get("ORCHESTRATOR_SCRIPTS_DIR", "")
    if not state_path or not repo_root or not scripts_dir:
        print("error: ORCHESTRATOR_STATE_YAML_PATH, REPO_ROOT, ORCHESTRATOR_SCRIPTS_DIR required", file=sys.stderr)
        return 1
    if not Path(state_path).is_file():
        print("ticket-rework: state.yaml not found", file=sys.stderr)
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing state.yaml"}}))
        return 1

    tickets_dir = Path(scripts_dir) / "tickets"
    ticket_common = tickets_dir / "ticket-common.sh"
    if not ticket_common.is_file():
        print("ticket-rework: ticket-common.sh not found", file=sys.stderr)
        return 1

    # Resolve repo root via ticket-common (bash helper).
    proc = subprocess.run(
        ["bash", "-c", f'source "{ticket_common}"; ticket_repo_root "{repo_root}"'],
        capture_output=True,
        text=True,
        executable="/bin/bash",
    )
    resolved_repo = (proc.stdout or repo_root).strip() or repo_root

    ticket_id, branch = ticket_rework_fields(load(state_path))
    if not ticket_id:
        print("ticket-rework: no ticket_id in state.yaml", file=sys.stderr)
        print(json.dumps({"status": "failed", "evidence": {"summary": "no ticket_id"}}))
        return 1

    backend_proc = subprocess.run(
        ["bash", "-c", f'source "{ticket_common}"; ticket_read_backend "{resolved_repo}"'],
        capture_output=True,
        text=True,
        executable="/bin/bash",
    )
    backend = (backend_proc.stdout or "").strip()
    target_status = "In Progress"

    if backend == "backlog":
        sync = tickets_dir / "ticket-sync-backlog.sh"
        subprocess.run(["bash", str(sync), ticket_id, target_status, resolved_repo], check=True)
    elif backend == "linear":
        sync = tickets_dir / "ticket-sync-linear.sh"
        subprocess.run(["bash", str(sync), ticket_id, target_status, resolved_repo], check=True)
    else:
        print("ticket-rework: unknown ticketing backend", file=sys.stderr)
        print(json.dumps({"status": "failed", "evidence": {"summary": "unknown ticketing backend"}}))
        return 1

    print(f"ticket: {ticket_id} → {target_status}", file=sys.stderr)
    if branch:
        print(f"branch: {branch} (retained — resume work there)", file=sys.stderr)

    print(
        json.dumps(
            {"status": "completed", "outputs": {"ticket_status_set": target_status}}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
