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
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


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
# FR-5: SKILL.md shells out to orchestrator run / run-workflow.sh (shell driver)
# ---------------------------------------------------------------------------

def test_fr5_skill_shell_driver():
    """skills/orchestrate/SKILL.md must delegate to the shell driver, not in-chat dispatch."""
    content = _read("skills/orchestrate/SKILL.md")

    assert "orchestrator run" in content, (
        "skills/orchestrate/SKILL.md must shell out via 'orchestrator run'."
    )

    assert "run-workflow.sh" in content, (
        "skills/orchestrate/SKILL.md must reference run-workflow.sh as the execution driver."
    )

    assert "run_in_background: true" not in content, (
        "skills/orchestrate/SKILL.md still documents in-chat Task-tool spawn semantics."
    )


# ---------------------------------------------------------------------------
# FR-6: developer.md prohibits direct state.yaml edits
# ---------------------------------------------------------------------------

def test_fr6_agents_forbid_state_edits():
    """developer.md must contain `orchestrator done` and a prohibition phrase
    (NOT) near state.yaml edits.
    """
    for agent_file in ("skills/developer/SKILL.md",):
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
# FR-9: SKILL.md has no in-chat dispatch loop (shell-out model)
# ---------------------------------------------------------------------------

def test_fr9_skill_no_chat_driver_dispatch():
    """skills/orchestrate/SKILL.md must not document the removed chat-driver loop."""
    content = _read("skills/orchestrate/SKILL.md")

    assert "### 3. Dispatch loop" not in content, (
        "skills/orchestrate/SKILL.md still has the in-chat dispatch-loop section."
    )
    assert "exit_code, stdout = orchestrator next" not in content, (
        "skills/orchestrate/SKILL.md still documents the in-chat orchestrator next loop."
    )

    assert "USAGE CAPTURE" not in content, (
        "skills/orchestrate/SKILL.md still contains 'USAGE CAPTURE'."
    )

    assert "MANDATORY: AGENT IDENTITY" not in content, (
        "skills/orchestrate/SKILL.md still contains driver agentId extraction prose."
    )


# ---------------------------------------------------------------------------
# FR-10: workflow-report contract uses the directory script form
# ---------------------------------------------------------------------------

def test_fr10_workflow_report_path():
    """config/steps/workflow-report/contract.yaml run: must point to the step script."""
    content = _read("config/steps/workflow-report/contract.yaml")

    assert "run: script.sh" in content, (
        "config/steps/workflow-report/contract.yaml run: must be 'script.sh' (directory form)."
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
    # execute-next-task removed in ORC-65 T-9; no longer a step contract.
    "ux-design",
    "run-phase-review",
    "generate-project-yaml",
    "install-tooling",
    "run-ux-critique",
]

# Known top-level state.raw bootstrap keys an input may resolve against.
_STATE_RAW_BOOTSTRAP_KEYS = {
    "change_id", "slug", "schema", "repo_root", "worktree_path", "branch",
    "phase", "complexity", "user_request", "tasks_path",
}

# Inline steps emit outputs at runtime via stdout JSON, not a static
# contract `outputs:` declaration. A required input produced by one of these
# resolves against runtime evidence.outputs at dispatch (design.md OQ-2).
_INLINE_RUNTIME_PRODUCERS = {
    "detect-language": {"languages", "package_manager", "web_project",
                        "backend_project", "scripts_added"},
    "install-tooling": {"scripts_added", "tools_installed"},

}


def _contract_path(step_id: str) -> str | None:
    """Return the path to a step's contract file (directory form preferred over flat form)."""
    dir_form = os.path.join(_REPO_ROOT, "config", "steps", step_id, "contract.yaml")
    flat_form = os.path.join(_REPO_ROOT, "config", "steps", f"{step_id}.yaml")
    if os.path.isfile(dir_form):
        return dir_form
    if os.path.isfile(flat_form):
        return flat_form
    return None


def _load_contract_yaml(step_id: str) -> dict:
    """Load a step contract, preferring the directory form (contract.yaml) over the flat form."""
    path = _contract_path(step_id)
    if path is None:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _is_typed_io(item: object) -> bool:
    """Return True for a valid typed I/O dict: {name, path} with optional 'optional' key."""
    if not isinstance(item, dict):
        return False
    return "name" in item and "path" in item


def test_orc63_pruned_contracts_have_no_prose_or_mappings():
    """No inputs:/outputs: item in the nine ORC-63 contracts contains '(' or
    parses as a YAML mapping (other than valid typed-IO dicts {name, path});
    none declares phase_context_bundle."""
    offenders = []
    for step_id in _ORC63_PRUNED_CONTRACTS:
        data = _load_contract_yaml(step_id)
        for key in ("inputs", "outputs"):
            for item in (data.get(key) or []):
                if isinstance(item, dict):
                    # Typed I/O dicts {name, path} are the new canonical form — skip.
                    if _is_typed_io(item):
                        continue
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
    # Check both flat-file contracts (e.g. select-workflow.yaml) and directory-form
    # contracts (<id>/contract.yaml).
    candidates = sorted(glob.glob(os.path.join(_REPO_ROOT, "config", "steps", "*.yaml")))
    candidates += sorted(glob.glob(os.path.join(_REPO_ROOT, "config", "steps", "*", "contract.yaml")))
    for path in candidates:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            continue
        for item in (data.get("inputs") or []):
            if isinstance(item, str) and item == "phase_context_bundle":
                offenders.append(os.path.relpath(path, os.path.join(_REPO_ROOT, "config", "steps")))
    assert not offenders, (
        f"phase_context_bundle still declared in: {offenders}"
    )


@pytest.mark.skip(
    reason="ORC-104: contract inputs/outputs stripped to prose in prompt.md; "
    "named-handle dataflow integrity is no longer statically checkable. See ORC-104 ticket."
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
        if _contract_path(step_id) is None:
            continue
        data = _load_contract_yaml(step_id)
        # Required inputs = inputs minus optional-annotated items.
        for item in (data.get("inputs") or []):
            if isinstance(item, dict):
                # Typed I/O dicts {name, path} are file-path–based — no named producer needed.
                if _is_typed_io(item):
                    continue
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


@pytest.mark.skip(
    reason="ORC-104: contract inputs/outputs stripped to prose in prompt.md; "
    "named-handle dataflow integrity is no longer statically checkable. See ORC-104 ticket."
)
def test_bugfix_schema_required_inputs_have_a_producer():
    """For the bugfix schema, every required input resolves to an upstream producer."""
    schema = yaml.safe_load(_read("config/workflows/bugfix.yaml"))
    step_ids = [
        (e if isinstance(e, str) else e.get("id"))
        for e in schema.get("steps", [])
    ]
    step_ids = [s.split(" if ")[0].strip() for s in step_ids if s]

    available: set[str] = set(_STATE_RAW_BOOTSTRAP_KEYS)
    unresolved = []
    for step_id in step_ids:
        if _contract_path(step_id) is None:
            continue
        data = _load_contract_yaml(step_id)
        for item in (data.get("inputs") or []):
            if isinstance(item, dict):
                # Typed I/O dicts {name, path} are file-path–based — no named producer needed.
                if _is_typed_io(item):
                    continue
                if len(item) == 1 and str(next(iter(item.values()))).strip().lower() == "optional":
                    continue
                unresolved.append(f"{step_id}: non-string input {item!r}")
                continue
            if not isinstance(item, str):
                continue
            if item not in available:
                unresolved.append(f"{step_id}: required input {item!r} has no producer")
        for out in (data.get("outputs") or []):
            if isinstance(out, str):
                available.add(out)
        if step_id in _INLINE_RUNTIME_PRODUCERS:
            available |= _INLINE_RUNTIME_PRODUCERS[step_id]

    assert not unresolved, (
        "bugfix schema producer/consumer integrity violations:\n  "
        + "\n  ".join(unresolved)
    )
