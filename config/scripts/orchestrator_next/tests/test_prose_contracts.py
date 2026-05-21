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


# ===========================================================================
# ORC-63 T-18: contract inputs/outputs hygiene + producer/consumer integrity
# (AC-6, OQ-2). Mechanical change — this regression-guard stands in for a RED.
# ===========================================================================

# The nine contracts ORC-63 prunes/normalizes (design.md Component 7, AC-6).
_ORC63_PRUNED_CONTRACTS = [
    "design-and-draft-artifacts",
    "explore",
    "diagnose",
    "execute-next-task",
    "ux-design",
    "run-phase-review",
    "generate-project-yaml",
    "install-tooling",
    "run-ux-critique",
]

# Known top-level state.raw bootstrap keys an input may resolve against.
_STATE_RAW_BOOTSTRAP_KEYS = {
    "change_id", "slug", "schema", "repo_root", "worktree_path", "branch",
    "flags", "phase", "complexity", "user_request", "tasks_path",
}

# Inline steps emit outputs at runtime via stdout JSON, not a static
# contract `outputs:` declaration. A required input produced by one of these
# resolves against runtime evidence.outputs at dispatch (design.md OQ-2).
_INLINE_RUNTIME_PRODUCERS = {
    "detect-language": {"languages", "package_manager", "web_project",
                        "backend_project", "scripts_added"},
    "install-tooling": {"scripts_added", "tools_installed"},
}


def _load_contract_yaml(step_id: str) -> dict:
    full = os.path.join(_REPO_ROOT, "config", "steps", f"{step_id}.yaml")
    with open(full, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_orc63_pruned_contracts_have_no_prose_or_mappings():
    """No inputs:/outputs: item in the nine ORC-63 contracts contains '(' or
    parses as a YAML mapping; none declares phase_context_bundle."""
    offenders = []
    for step_id in _ORC63_PRUNED_CONTRACTS:
        data = _load_contract_yaml(step_id)
        for key in ("inputs", "outputs"):
            for item in (data.get(key) or []):
                if isinstance(item, dict):
                    offenders.append(f"{step_id}.{key}: mapping item {item!r}")
                elif isinstance(item, str):
                    if "(" in item:
                        offenders.append(f"{step_id}.{key}: prose item {item!r}")
                    if item == "phase_context_bundle":
                        offenders.append(f"{step_id}.{key}: phase_context_bundle")
    assert not offenders, (
        "ORC-63 contract hygiene violations:\n  " + "\n  ".join(offenders)
    )


def test_no_contract_declares_phase_context_bundle():
    """phase_context_bundle appears in no contract inputs: across config/steps/."""
    import glob
    offenders = []
    for path in sorted(glob.glob(os.path.join(_REPO_ROOT, "config", "steps", "*.yaml"))):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            continue
        for item in (data.get("inputs") or []):
            if isinstance(item, str) and item == "phase_context_bundle":
                offenders.append(os.path.basename(path))
    assert not offenders, (
        f"phase_context_bundle still declared in: {offenders}"
    )


def test_feature_schema_required_inputs_have_a_producer():
    """For the feature schema, every required (non-optional) input resolves to
    an upstream contract outputs: entry, a state.raw bootstrap key, or an
    earlier inline step's runtime output."""
    schema = yaml.safe_load(_read("config/workflows/feature.yaml"))
    step_ids = [
        (e if isinstance(e, str) else e.get("id"))
        for e in schema.get("steps", [])
    ]
    step_ids = [s.split(" if ")[0].strip() for s in step_ids if s]

    available: set[str] = set(_STATE_RAW_BOOTSTRAP_KEYS)
    unresolved = []
    for step_id in step_ids:
        contract_path = os.path.join(_REPO_ROOT, "config", "steps", f"{step_id}.yaml")
        if not os.path.isfile(contract_path):
            continue
        data = _load_contract_yaml(step_id)
        # Required inputs = inputs minus optional-annotated items.
        for item in (data.get("inputs") or []):
            if isinstance(item, dict):
                # An optional-annotated {name: optional} item is never required.
                if len(item) == 1 and str(next(iter(item.values()))).strip().lower() == "optional":
                    continue
                unresolved.append(f"{step_id}: non-string input {item!r}")
                continue
            if not isinstance(item, str):
                continue
            if item not in available:
                unresolved.append(f"{step_id}: required input {item!r} has no producer")
        # This step's declared outputs become available to downstream steps.
        for out in (data.get("outputs") or []):
            if isinstance(out, str):
                available.add(out)
        # Inline runtime producers contribute their well-known outputs.
        if step_id in _INLINE_RUNTIME_PRODUCERS:
            available |= _INLINE_RUNTIME_PRODUCERS[step_id]

    assert not unresolved, (
        "feature schema producer/consumer integrity violations:\n  "
        + "\n  ".join(unresolved)
    )
