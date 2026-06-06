"""
T-6: Regression test — all step contracts referenced in any workflow must declare
either `agent:` or `run:`.

This test FAILS against unmigrated contracts (T-7 + T-8 will fix them).
After T-7 and T-8, the test PASSES (verified in T-10).

Excluded by design: select-workflow (per design.md Non-Goals).
"""
from __future__ import annotations

import os
import glob
from typing import Set

import pytest
import yaml

_CONFIG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
_STEPS_DIR = os.path.join(_CONFIG_DIR, "steps")
_WORKFLOWS_DIR = os.path.join(_CONFIG_DIR, "workflows")

# Per design.md Non-Goals: select-workflow is never dispatched and excluded.
_EXCLUDED_STEPS: Set[str] = {"select-workflow"}


def _collect_workflow_steps() -> Set[str]:
    """Return set of step IDs referenced in any workflow YAML."""
    step_ids: Set[str] = set()
    for wf_path in glob.glob(os.path.join(_WORKFLOWS_DIR, "*.yaml")):
        with open(wf_path) as f:
            data = yaml.safe_load(f)
        if not data:
            continue
        steps = data.get("steps") or []
        for entry in steps:
            if isinstance(entry, dict):
                sid = entry.get("id", "")
            else:
                sid = str(entry).split(" if ")[0].strip()
            if sid:
                step_ids.add(sid)
    return step_ids


def _load_contract(step_id: str) -> dict | None:
    """Load a step contract YAML, return None if not found."""
    dir_path = os.path.join(_STEPS_DIR, step_id, "contract.yaml")
    flat_path = os.path.join(_STEPS_DIR, f"{step_id}.yaml")
    if os.path.isfile(dir_path):
        path = dir_path
    elif os.path.isfile(flat_path):
        path = flat_path
    else:
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def test_all_workflow_steps_have_agent_or_run():
    """Every step referenced in any workflow must declare agent: or run:."""
    step_ids = _collect_workflow_steps()
    assert step_ids, "No step IDs found in any workflow — check workflow YAML format"

    violations: list[str] = []
    missing_contracts: list[str] = []

    for step_id in sorted(step_ids):
        if step_id in _EXCLUDED_STEPS:
            continue

        contract = _load_contract(step_id)
        if contract is None:
            missing_contracts.append(step_id)
            continue

        has_agent = bool(contract.get("agent"))
        has_run = bool(contract.get("run"))

        if not has_agent and not has_run:
            violations.append(step_id)

    error_lines = []
    if missing_contracts:
        error_lines.append(
            f"Contracts not found (need creation): {missing_contracts}"
        )
    if violations:
        error_lines.append(
            f"Contracts missing both agent: and run: ({len(violations)} unmigrated):\n"
            + "\n".join(f"  - {s}" for s in violations)
        )

    assert not error_lines, "\n".join(error_lines)


_BANNED_SCRIPT_PROTOCOL_KEYS = frozenset(
    {"inputs", "outputs", "rules", "instruction", "verify"}
)


def test_script_contracts_have_no_agent_protocol_fields():
    """Script-kind contracts must not declare agent-protocol surface keys."""
    violations: list[tuple[str, list[str]]] = []

    for contract_path in sorted(
        glob.glob(os.path.join(_STEPS_DIR, "*", "contract.yaml"))
    ):
        with open(contract_path) as f:
            contract = yaml.safe_load(f) or {}

        if contract.get("kind") != "script":
            continue

        contract_id = contract.get("id") or os.path.basename(
            os.path.dirname(contract_path)
        )
        banned_present = sorted(_BANNED_SCRIPT_PROTOCOL_KEYS & contract.keys())
        if banned_present:
            violations.append((contract_id, banned_present))

    if violations:
        lines = [
            "Script-kind contracts must not declare agent-protocol fields "
            f"{sorted(_BANNED_SCRIPT_PROTOCOL_KEYS)}:",
        ]
        for contract_id, keys in violations:
            lines.append(f"  - {contract_id}: {', '.join(keys)}")
        assert violations == [], "\n".join(lines)
