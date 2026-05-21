"""T-8: RED grep-assertion tests for prose and contract fixes.

Seven tests, one per FR. All should FAIL before T-9/T-10/T-11/T-12 apply edits.
"""
from __future__ import annotations

import os
import re

import yaml
import pytest

# Repo root is 4 levels above this file:
# tests/ -> orchestrator_next/ -> scripts/ -> config/ -> <repo_root>
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))


def _read(rel_path: str) -> str:
    full = os.path.join(_REPO_ROOT, rel_path)
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# FR-1 removed: workflow init is pre-dispatch script execution, so there is no
# workflow-init agent or dispatched step contract to validate here. The
# workflow_plan `active:` shape is enforced by generate_plan and its own tests.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FR-3: preview-route.yaml outputs uses bareword `route_preview`, not prose phrase
# ---------------------------------------------------------------------------

def test_fr3_preview_route_script_path():
    """config/steps/preview-route.yaml run: must point to the inline script.

    Must NOT contain the literal phrase 'state.yaml route_preview block'.
    Must contain run: pointing to scripts/inline/preview-route.sh.
    """
    content = _read("config/steps/preview-route.yaml")

    # Must NOT contain the prose phrase
    assert "state.yaml route_preview block" not in content, (
        "config/steps/preview-route.yaml still contains 'state.yaml route_preview block'. "
    )

    # Must contain run: pointing to the inline script
    assert "scripts/inline/preview-route.sh" in content, (
        "config/steps/preview-route.yaml run: must reference 'scripts/inline/preview-route.sh'."
    )


# ---------------------------------------------------------------------------
# FR-4: spec/project.yaml verify_commands.test starts with pytest
# ---------------------------------------------------------------------------

def test_fr4_project_verify_commands():
    """spec/project.yaml must have verify_commands.test as non-empty string starting with 'pytest'."""
    content = _read("spec/project.yaml")
    data = yaml.safe_load(content)

    verify_commands = data.get("verify_commands")
    assert verify_commands and not isinstance(verify_commands, list), (
        "spec/project.yaml verify_commands is empty or a list — must be a mapping "
        "with a 'test' key."
    )

    test_cmd = verify_commands.get("test", "")
    assert isinstance(test_cmd, str) and test_cmd.strip().startswith("pytest"), (
        f"spec/project.yaml verify_commands.test must be a non-empty string starting "
        f"with 'pytest', got: {test_cmd!r}"
    )


# ---------------------------------------------------------------------------
# FR-5: SKILL.md mentions run_in_background: true AND exception agents
# ---------------------------------------------------------------------------

def test_fr5_skill_background_spawn():
    """skills/orchestrate/SKILL.md must mention run_in_background: true and both exception agents."""
    content = _read("skills/orchestrate/SKILL.md")

    assert "run_in_background: true" in content, (
        "skills/orchestrate/SKILL.md does not mention 'run_in_background: true'. "
        "Add spawn semantics annotation in §4 run_step branch."
    )

    assert "ideator" in content, (
        "skills/orchestrate/SKILL.md does not mention 'ideator' as an exception agent "
        "for foreground spawning."
    )

    assert "reviewer" in content, (
        "skills/orchestrate/SKILL.md does not mention 'reviewer' as an exception agent "
        "for foreground spawning."
    )


# ---------------------------------------------------------------------------
# FR-6: developer.md prohibits direct state.yaml edits
# ---------------------------------------------------------------------------

def test_fr6_agents_forbid_state_edits():
    """developer.md must contain `orchestrator done` and a prohibition phrase
    (NOT) near state.yaml edits.
    """
    for agent_file in ("agents/developer.md",):
        content = _read(agent_file)

        assert "orchestrator done" in content, (
            f"{agent_file} does not contain 'orchestrator done'. "
            "Update the State Updates section to reference 'orchestrator done' (FR-9 Stage B migration)."
        )

        # Check for a prohibition: "NOT" or "MUST NOT" near state.yaml
        # Pattern: "NOT" within 120 chars of "state.yaml" (same sentence/clause)
        # We scan for occurrences of "state.yaml" and check surrounding context
        found_prohibition = False
        for m in re.finditer(r"state\.yaml", content, re.IGNORECASE):
            start = max(0, m.start() - 120)
            end = min(len(content), m.end() + 120)
            snippet = content[start:end]
            if re.search(r"\bNOT\b", snippet):
                found_prohibition = True
                break

        assert found_prohibition, (
            f"{agent_file} does not contain a prohibition (NOT) near 'state.yaml'. "
            "Add 'MUST NOT directly edit state.yaml' to the State Updates section."
        )


# ---------------------------------------------------------------------------
# FR-9: SKILL.md passes raw Task result as agent_task_result (not driver parsing)
# ---------------------------------------------------------------------------

def test_fr9_skill_passes_agent_task_result():
    """skills/orchestrate/SKILL.md must instruct the driver to pass agent_task_result
    and must not require driver-side USAGE CAPTURE or agentId extraction.
    """
    content = _read("skills/orchestrate/SKILL.md")

    assert "agent_task_result" in content, (
        "skills/orchestrate/SKILL.md does not contain 'agent_task_result'. "
        "Driver must pass raw Task tool result text; record.py extracts agentId."
    )

    assert "USAGE CAPTURE" not in content, (
        "skills/orchestrate/SKILL.md still contains 'USAGE CAPTURE'. "
        "Remove driver-side usage parsing; record.py loads usage from JSONL."
    )

    assert "MANDATORY: AGENT IDENTITY" not in content, (
        "skills/orchestrate/SKILL.md still contains driver agentId extraction. "
        "record.py extracts agentId from agent_task_result."
    )


# ---------------------------------------------------------------------------
# FR-10: compute-swe-metrics.yaml uses the /inline/ path
# ---------------------------------------------------------------------------

def test_fr10_compute_swe_metrics_path():
    """config/steps/compute-swe-metrics.yaml run: must point to scripts/inline/compute-swe-metrics.sh."""
    content = _read("config/steps/compute-swe-metrics.yaml")

    assert "scripts/inline/compute-swe-metrics.sh" in content, (
        "config/steps/compute-swe-metrics.yaml run: must reference 'scripts/inline/compute-swe-metrics.sh'."
    )
