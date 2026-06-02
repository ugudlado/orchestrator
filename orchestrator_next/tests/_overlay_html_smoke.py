#!/usr/bin/env python3
"""Smoke test: overlay graph + render_html carries labels and sidebar data (ORC-122 AC-5)."""
from __future__ import annotations

import os
import sys
import tempfile

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrator_next.graph import render_html, render_workflow_graph_with_overlay  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = os.path.join(tmp, ".orchestrator", "demo")
        os.makedirs(state_dir, exist_ok=True)
        state_path = os.path.join(state_dir, "20260101T000000_feature_state.yaml")
        state = {
            "change_id": "demo",
            "schema": "feature",
            "step_history": [
                {
                    "step_id": "implement-tasks",
                    "attempt": 1,
                    "status": "completed",
                    "usage": {
                        "input_tokens": 8000,
                        "output_tokens": 1278,
                        "cost_usd": 1.05,
                    },
                }
            ],
        }
        with open(state_path, "w", encoding="utf-8") as f:
            yaml.dump(state, f)

        mermaid_src, step_data = render_workflow_graph_with_overlay("feature", state_dir)
        html = render_html(mermaid_src, "feature / demo — cost overlay", step_data)

        checks = [
            ("tok ·" in html, "overlay label 'tok ·' missing from HTML"),
            ('"implement-tasks"' in html, 'step_id key "implement-tasks" missing from STEP_DATA JSON'),
            ("showStep" in html, "showStep click binding missing from HTML"),
        ]
        for ok, msg in checks:
            if not ok:
                print(f"FAIL: {msg}", file=sys.stderr)
                return 1

    print("OK: overlay HTML smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
