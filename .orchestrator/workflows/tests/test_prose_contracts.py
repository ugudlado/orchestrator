"""T-8: RED grep-assertion tests for prose and contract fixes.

Seven tests, one per FR. All should FAIL before T-9/T-10/T-11/T-12 apply edits.
"""
from __future__ import annotations

import os
import re

import yaml

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


def test_fr5_skill_shell_driver():
    """skills/archive/orchestrate/SKILL.md must delegate to the CLI, not in-chat dispatch."""
    content = _read("skills/archive/orchestrate/SKILL.md")

    assert "orchestrator run" in content, (
        "skills/archive/orchestrate/SKILL.md must shell out via 'orchestrator run'."
    )

    # The dispatch loop runs in-process in the CLI (run_loop.py), not the old
    # shell driver. The skill must reference the in-process loop, not run-workflow.sh.
    assert "run_loop" in content or "in-process" in content, (
        "skills/archive/orchestrate/SKILL.md must reference the in-process dispatch loop."
    )

    assert "run_in_background: true" not in content, (
        "skills/archive/orchestrate/SKILL.md still documents in-chat Task-tool spawn semantics."
    )


# ---------------------------------------------------------------------------
# FR-6: skill-steps complete via COMPLETION / orchestrator done
# ---------------------------------------------------------------------------

def test_fr6_skill_steps_use_orchestrator_done():
    """Skill charters must document COMPLETION / orchestrator done as the
    completion protocol (replaces the removed skills/developer agent).
    """
    for skill_file in (
        "skills/architect/SKILL.md",
        "skills/developer/SKILL.md",
        "skills/ux-designer/SKILL.md",
    ):
        content = _read(skill_file)
        assert "orchestrator done" in content or "COMPLETION:" in content, (
            f"{skill_file} must document COMPLETION / orchestrator done "
            "(skill-step completion protocol)."
        )


# ---------------------------------------------------------------------------
# FR-9: SKILL.md has no in-chat dispatch loop (shell-out model)
# ---------------------------------------------------------------------------

def test_fr9_skill_no_chat_driver_dispatch():
    """skills/archive/orchestrate/SKILL.md must not document the removed chat-driver loop."""
    content = _read("skills/archive/orchestrate/SKILL.md")

    assert "### 3. Dispatch loop" not in content, (
        "skills/archive/orchestrate/SKILL.md still has the in-chat dispatch-loop section."
    )
    assert "exit_code, stdout = orchestrator next" not in content, (
        "skills/archive/orchestrate/SKILL.md still documents the in-chat orchestrator next loop."
    )

    assert "USAGE CAPTURE" not in content, (
        "skills/archive/orchestrate/SKILL.md still contains 'USAGE CAPTURE'."
    )

    assert "MANDATORY: AGENT IDENTITY" not in content, (
        "skills/archive/orchestrate/SKILL.md still contains driver agentId extraction prose."
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
    "design",
    "explore",
    "diagnose",
    # execute-next-task removed in ORC-65 T-9; no longer a step contract.
    "ux-design",
    "code-review",
    "generate-project-yaml",
    "install-tooling",
    "ux-critique",
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
