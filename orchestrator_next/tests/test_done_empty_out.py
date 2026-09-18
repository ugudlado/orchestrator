"""`done --out {}` against an all-optional contract must complete, not reject.

The Mod falls back to an empty `out` when a judgment subagent's final message
carries no parseable JSON block: the engine, not the harness, is what knows
whether the step actually produced what its contract asked for. `learn`'s
contract is the live case — one optional artifact out, written to disk by the
step itself — so an empty payload is a complete result, while a contract with
a required out must still be rejected so the Mod can record the abandon.
"""
from __future__ import annotations

import types
from pathlib import Path

from orchestrator_next.protocol import validate_out

# `learn`'s own out: block (.orchestrator/workflows/steps/learn/contract.yaml).
LEARN_OUTS = {
    "proposed_scenarios": {"artifact": "proposed-scenarios.jsonl", "optional": True}
}


def _contract(outputs: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(outputs=outputs)


def _raw(base: Path) -> dict:
    """A state whose artifacts resolve into `base`.

    `artifacts_root` is the recipe-declared override `_artifact_base` reads
    (protocol.py `_recipe_artifacts_root`); an absolute one is used as-is, so
    this pins the artifact checks to a tmp dir rather than the real run store.
    """
    return {"artifacts_root": str(base)}


def test_empty_out_satisfies_an_optional_artifact_written_on_disk(tmp_path: Path) -> None:
    """The live `learn` case: the file is there, the JSON block was not."""
    (tmp_path / "proposed-scenarios.jsonl").write_text('{"a": 1}\n')

    assert validate_out(_contract(LEARN_OUTS), {}, _raw(tmp_path)) == []


def test_empty_out_satisfies_an_optional_artifact_that_was_never_written(
    tmp_path: Path,
) -> None:
    """Optional means optional: a step that produced nothing still completed."""
    assert validate_out(_contract(LEARN_OUTS), {}, _raw(tmp_path)) == []


def test_empty_out_is_rejected_when_the_contract_requires_an_out(tmp_path: Path) -> None:
    """A required out is what makes the Mod's fallback record an abandon."""
    contract = _contract({"verdict": {"type": "enum", "values": ["pass", "needs_work"]}})
    problems = validate_out(contract, {}, _raw(tmp_path))

    assert len(problems) == 1
    assert "out.verdict" in problems[0]


def test_empty_out_is_rejected_when_a_required_artifact_is_missing(tmp_path: Path) -> None:
    contract = _contract({"design": {"artifact": "design.md"}})
    problems = validate_out(contract, {}, _raw(tmp_path))

    assert len(problems) == 1
    assert "artifact not found" in problems[0]


def test_a_required_artifact_present_on_disk_needs_no_out_key(tmp_path: Path) -> None:
    """The path is derivable from the contract, so the key is not the evidence."""
    (tmp_path / "design.md").write_text("# design\n")
    contract = _contract({"design": {"artifact": "design.md"}})

    assert validate_out(contract, {}, _raw(tmp_path)) == []


def test_a_contract_declaring_no_outs_accepts_an_empty_payload(tmp_path: Path) -> None:
    assert validate_out(_contract({}), {}, _raw(tmp_path)) == []
