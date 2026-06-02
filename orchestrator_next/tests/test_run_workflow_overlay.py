"""T-3: source-contract test for run-workflow.sh overlay append seam (AC-3, AC-4)."""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_RUN_WORKFLOW = os.path.join(_REPO_ROOT, "orchestrator_next", "scripts", "run-workflow.sh")


def _read_run_workflow() -> str:
    with open(_RUN_WORKFLOW, "r", encoding="utf-8") as f:
        return f.read()


def test_run_workflow_invokes_agent_overlay_after_build_prompt() -> None:
    content = _read_run_workflow()
    build_idx = content.find("PROMPT=$(build_prompt")
    overlay_idx = content.find("orchestrator_next.agent_overlay")
    prompt_file_idx = content.find('echo "$PROMPT" > "$PROMPT_FILE"')

    assert build_idx != -1, "run-workflow.sh must call build_prompt to assemble PROMPT"
    assert overlay_idx != -1, (
        "run-workflow.sh must invoke orchestrator_next.agent_overlay to load repo overlay"
    )
    assert prompt_file_idx != -1, "run-workflow.sh must write PROMPT to PROMPT_FILE"
    assert build_idx < overlay_idx < prompt_file_idx, (
        "agent_overlay invocation must appear after build_prompt and before PROMPT_FILE write"
    )


def test_run_workflow_overlay_uses_repo_root_and_agent() -> None:
    content = _read_run_workflow()
    overlay_lines = [
        line for line in content.splitlines() if "orchestrator_next.agent_overlay" in line
    ]
    assert overlay_lines, "run-workflow.sh must invoke orchestrator_next.agent_overlay"
    overlay_line = overlay_lines[0]
    assert '"$REPO_ROOT"' in overlay_line, "overlay invocation must pass $REPO_ROOT"
    assert '"$AGENT"' in overlay_line, "overlay invocation must pass $AGENT"
