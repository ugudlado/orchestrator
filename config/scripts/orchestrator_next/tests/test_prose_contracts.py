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
# FR-1: workflow-init.md has `active:` as YAML key, not `active_steps:`
# ---------------------------------------------------------------------------

def test_fr1_workflow_init_active_key():
    """agents/workflow-init.md must have `active:` as an indented YAML dict key.

    The pattern (^|\\n)\\s+active: matches `active:` with leading whitespace
    (i.e., as a YAML mapping key, not a bare word in prose).
    """
    content = _read("agents/workflow-init.md")

    # Must contain `active:` as an indented YAML key
    assert re.search(r"(^|\n)\s+active:", content), (
        "agents/workflow-init.md does not contain 'active:' as an indented YAML key. "
        "Add a canonical YAML example block showing the workflow_plan shape."
    )

    # Must NOT contain `active_steps:` as a YAML key (bare, with leading whitespace).
    # Appearances inside backtick inline-code spans (commentary) are allowed.
    # Strip all backtick-quoted spans before checking.
    stripped = re.sub(r"`[^`]*`", "", content)
    assert "active_steps:" not in stripped, (
        "agents/workflow-init.md contains 'active_steps:' as a bare key (not in "
        "backtick commentary). The dispatcher reads 'active:', not 'active_steps:'."
    )


# ---------------------------------------------------------------------------
# FR-3: preview-route.yaml outputs uses bareword `route_preview`, not prose phrase
# ---------------------------------------------------------------------------

def test_fr3_preview_route_output_identifier():
    """config/steps/preview-route.yaml must have `route_preview` as an outputs key.

    Must NOT contain the literal phrase 'state.yaml route_preview block'.
    Must contain `route_preview` as a bareword outputs entry.
    """
    content = _read("config/steps/preview-route.yaml")

    # Must NOT contain the prose phrase
    assert "state.yaml route_preview block" not in content, (
        "config/steps/preview-route.yaml still contains 'state.yaml route_preview block'. "
        "Replace with the bareword `route_preview` in the outputs list."
    )

    # Must contain `route_preview` as a bareword (outputs entry)
    assert re.search(r"^\s*-\s+route_preview\s*$", content, re.MULTILINE), (
        "config/steps/preview-route.yaml does not contain `route_preview` as a "
        "list entry under outputs. Add `- route_preview` to the outputs section."
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
# FR-6: developer.md and workflow-init.md both prohibit direct state.yaml edits
# ---------------------------------------------------------------------------

def test_fr6_agents_forbid_state_edits():
    """developer.md and workflow-init.md must contain `orchestrator record` and
    a prohibition phrase (NOT) near state.yaml edits.
    """
    for agent_file in ("agents/developer.md", "agents/workflow-init.md"):
        content = _read(agent_file)

        assert "orchestrator record" in content, (
            f"{agent_file} does not contain 'orchestrator record'. "
            "Add a State Updates section referencing the record command."
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
# FR-9: SKILL.md has MANDATORY USAGE CAPTURE section with input_tokens assertion
# ---------------------------------------------------------------------------

def test_fr9_skill_usage_capture_mandatory():
    """skills/orchestrate/SKILL.md must contain MANDATORY and USAGE CAPTURE in the
    same section, plus a reference to usage.input_tokens as a post-record assertion.
    """
    content = _read("skills/orchestrate/SKILL.md")

    # Both tokens must appear in the file
    assert "MANDATORY" in content, (
        "skills/orchestrate/SKILL.md does not contain 'MANDATORY'. "
        "Add '3. MANDATORY: USAGE CAPTURE' numbered step in §4 run_step."
    )

    assert "USAGE CAPTURE" in content, (
        "skills/orchestrate/SKILL.md does not contain 'USAGE CAPTURE'. "
        "Add '3. MANDATORY: USAGE CAPTURE' numbered step in §4 run_step."
    )

    # Both must appear within the same section (within 500 chars of each other)
    idx_mandatory = content.find("MANDATORY")
    idx_capture = content.find("USAGE CAPTURE")
    assert abs(idx_mandatory - idx_capture) < 500, (
        f"'MANDATORY' (pos {idx_mandatory}) and 'USAGE CAPTURE' (pos {idx_capture}) "
        f"are too far apart ({abs(idx_mandatory - idx_capture)} chars). "
        "They must appear in the same section."
    )

    # Must reference usage.input_tokens as a post-record assertion
    assert "usage.input_tokens" in content, (
        "skills/orchestrate/SKILL.md does not reference 'usage.input_tokens'. "
        "Add post-record assertion: 'assert step_history[-1].usage.input_tokens is non-null'."
    )


# ---------------------------------------------------------------------------
# FR-10: compute-swe-metrics.yaml uses the /inline/ path
# ---------------------------------------------------------------------------

def test_fr10_compute_swe_metrics_path():
    """config/steps/compute-swe-metrics.yaml must reference scripts/inline/compute-swe-metrics.sh.
    Must NOT contain the buggy path without /inline/.
    """
    content = _read("config/steps/compute-swe-metrics.yaml")

    # Must NOT contain the old incorrect path (without /inline/)
    assert "$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh" not in content, (
        "config/steps/compute-swe-metrics.yaml still references "
        "'$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh' (missing /inline/). "
        "Change to '$ORCHESTRATOR_HOME/scripts/inline/compute-swe-metrics.sh'."
    )

    # Must contain the correct path
    assert "$ORCHESTRATOR_HOME/scripts/inline/compute-swe-metrics.sh" in content, (
        "config/steps/compute-swe-metrics.yaml does not reference "
        "'$ORCHESTRATOR_HOME/scripts/inline/compute-swe-metrics.sh'. "
        "Update instruction step 2a with the correct path."
    )
