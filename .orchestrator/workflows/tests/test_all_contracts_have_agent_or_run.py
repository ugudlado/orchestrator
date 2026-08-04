"""
Regression — every workflow step must have a contract with run: | prompt:,
and every prompt step must be listed under step_models: in models.yaml.
"""
from __future__ import annotations

import glob
import os
from typing import Set

import yaml

from orchestrator_next.parser import resolve_prompt_file, ContractError
from orchestrator_next.workflow_steps import step_id_of

_CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_STEPS_DIR = os.path.join(_CONFIG_DIR, "steps")
_SKILLS_DIR = os.path.abspath(os.path.join(_CONFIG_DIR, "..", "skills"))
_WORKFLOWS_DIR = os.path.join(_CONFIG_DIR, "workflows")
_MODELS_YAML = os.path.join(_CONFIG_DIR, "models.yaml")

_EXCLUDED_STEPS: Set[str] = {"select-workflow"}


def _collect_workflow_steps() -> Set[str]:
    step_ids: Set[str] = set()
    for wf_path in glob.glob(os.path.join(_WORKFLOWS_DIR, "*.yaml")):
        with open(wf_path) as f:
            data = yaml.safe_load(f)
        if not data:
            continue
        for entry in data.get("steps") or []:
            sid = step_id_of(entry)
            if sid:
                step_ids.add(sid)
    return step_ids


def _load_contract(step_id: str) -> dict | None:
    dir_path = os.path.join(_STEPS_DIR, step_id, "contract.yaml")
    if not os.path.isfile(dir_path):
        return None
    with open(dir_path) as f:
        return yaml.safe_load(f)


def _step_models() -> dict:
    with open(_MODELS_YAML) as f:
        data = yaml.safe_load(f) or {}
    sm = data.get("step_models") or {}
    return sm if isinstance(sm, dict) else {}


def test_all_workflow_steps_have_run_or_prompt(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", _SKILLS_DIR)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", _STEPS_DIR)
    step_ids = _collect_workflow_steps()
    assert step_ids, "No step IDs found in any workflow"

    violations: list[str] = []
    missing_contracts: list[str] = []
    missing_charter: list[str] = []
    missing_step_models: list[str] = []
    banned_model_keys: list[str] = []
    step_models = _step_models()

    for step_id in sorted(step_ids):
        if step_id in _EXCLUDED_STEPS:
            continue

        contract = _load_contract(step_id)
        if contract is None:
            missing_contracts.append(step_id)
            continue

        has_run = bool(contract.get("run"))
        has_skill = bool(contract.get("skill"))
        has_prompt = bool(contract.get("prompt"))
        has_model = "model" in contract

        if has_skill:
            violations.append(step_id)
            continue
        kinds = sum(bool(x) for x in (has_run, has_prompt))
        if kinds != 1:
            violations.append(step_id)
            continue
        if has_model:
            banned_model_keys.append(step_id)
            continue
        if has_prompt and step_id not in step_models:
            missing_step_models.append(step_id)
            continue

        if has_prompt:
            local = os.path.join(_STEPS_DIR, step_id, contract["prompt"])
            if os.path.isfile(local):
                continue
            try:
                resolve_prompt_file(contract["prompt"])
            except ContractError as exc:
                missing_charter.append(f"{step_id} -> {exc}")

    error_lines = []
    if missing_contracts:
        error_lines.append(f"Contracts not found: {missing_contracts}")
    if violations:
        error_lines.append(
            "Contracts must declare exactly one of run: | prompt: "
            "(skill: is removed):\n"
            + "\n".join(f"  - {s}" for s in violations)
        )
    if banned_model_keys:
        error_lines.append(
            "Contracts must not declare model: (use step_models: in models.yaml):\n"
            + "\n".join(f"  - {s}" for s in banned_model_keys)
        )
    if missing_step_models:
        error_lines.append(
            "Prompt steps missing from models.yaml step_models:\n"
            + "\n".join(f"  - {s}" for s in missing_step_models)
        )
    if missing_charter:
        error_lines.append(
            "Missing prompt files:\n"
            + "\n".join(f"  - {s}" for s in missing_charter)
        )

    assert not error_lines, "\n".join(error_lines)


_BANNED_SCRIPT_PROTOCOL_KEYS = frozenset(
    {"inputs", "outputs", "rules", "instruction", "verify"}
)


def test_script_contracts_have_no_agent_protocol_fields():
    violations: list[tuple[str, list[str]]] = []

    for contract_path in sorted(
        glob.glob(os.path.join(_STEPS_DIR, "*", "contract.yaml"))
    ):
        with open(contract_path) as f:
            contract = yaml.safe_load(f) or {}

        if not contract.get("run"):
            continue

        contract_id = contract.get("id") or os.path.basename(
            os.path.dirname(contract_path)
        )
        banned_present = sorted(_BANNED_SCRIPT_PROTOCOL_KEYS & contract.keys())
        if banned_present:
            violations.append((contract_id, banned_present))

    if violations:
        lines = [
            "Shell contracts must not declare agent-protocol fields "
            f"{sorted(_BANNED_SCRIPT_PROTOCOL_KEYS)}:",
        ]
        for contract_id, keys in violations:
            lines.append(f"  - {contract_id}: {', '.join(keys)}")
        assert violations == [], "\n".join(lines)


def test_no_skill_keys_in_steps_or_workflows():
    """T4 verify: grep-equivalent — no skill: left in live config."""
    from pathlib import Path

    hits: list[str] = []
    for base in (_STEPS_DIR, _WORKFLOWS_DIR):
        for path in sorted(Path(base).rglob("*.yaml")):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if "skill:" in line:
                    hits.append(f"{path}:{i}:{line.strip()}")
    assert hits == [], "skill: still present:\n" + "\n".join(hits)
