#!/usr/bin/env python3
"""Workflow report step — thin wrapper over orchestrator_next.report.

The report logic lives in the engine (`orchestrator report`); this step only
adapts it to the step protocol (JSON status line on stdout). The engine
package is located relative to this step's config dir so the wrapper works
from both a checkout (config/ beside orchestrator_next/) and an installed
package (config/ inside the orchestrator_next package).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent
for _candidate in (_CONFIG_DIR.parent, _CONFIG_DIR.parent.parent):
    if (_candidate / "orchestrator_next" / "report.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from orchestrator_next.report import report_for_state  # noqa: E402


def main() -> int:
    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    repo_root = os.environ.get("REPO_ROOT", "")

    if not state_path:
        sys.stderr.write("error: ORCHESTRATOR_STATE_YAML_PATH required\n")
        return 1

    payload = report_for_state(state_path, repo_root)
    if payload is None:
        print(json.dumps({"status": "failed", "evidence": {"summary": "missing state.yaml or change_id"}}))
        return 1

    print(json.dumps({"status": "completed", "outputs": payload}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
