"""Payload supplement helpers extracted from record.py (ORC-74)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator_next.parser import StepContract


def _coerce_payload_outputs(raw: Any) -> dict[str, Any]:
    """Normalize payload.outputs to a mapping for step_history evidence."""
    if isinstance(raw, dict):
        return raw
    if raw is not None:
        sys.stderr.write(
            f"[record] warning: outputs must be a mapping, got {type(raw).__name__}; "
            "treating as empty\n"
        )
    return {}


def _artifact_basenames_from_outputs(outputs: dict[str, Any]) -> list[str]:
    """Infer artifact filenames from COMPLETION output keys/values."""
    skip_keys = frozenset({
        "updated_artifact_set",
        "design_direction",
        "complexity",
        "discovery_result",
    })
    names: list[str] = []
    for key, val in outputs.items():
        if key in skip_keys:
            continue
        if isinstance(key, str) and "." in key and not key.startswith("{"):
            names.append(Path(key).name)
            continue
        if isinstance(val, str) and val.strip():
            candidate = Path(val.strip()).name
            if "." in candidate:
                names.append(candidate)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _supplement_legacy_outputs(
    outputs: dict[str, Any],
    payload: dict[str, Any],
    contract: StepContract | None,
) -> dict[str, Any]:
    """No-op: contract.outputs is no longer declared; kept for call-site compat."""
    return outputs



def _supplement_learn_result(
    outputs: dict[str, Any],
    payload: dict[str, Any],
    step_id: str,
    status: str,
) -> dict[str, Any]:
    """run-learn-cycle: ensure learn_result when the agent omitted outputs:."""
    if step_id != "run-learn-cycle":
        return outputs
    out = dict(outputs)
    cur = out.get("learn_result")
    if cur is not None and cur != "" and not (hasattr(cur, "__len__") and len(cur) == 0):
        return out
    if status != "completed":
        return out
    out["learn_result"] = {"completed": True}
    sys.stderr.write(
        "[record] supplemented outputs.learn_result for run-learn-cycle "
        "(COMPLETION missing outputs:; treated as learn completed)\n"
    )
    return out


def _supplement_backlog_tickets_synced(
    outputs: dict[str, Any],
    step_id: str,
    status: str,
) -> dict[str, Any]:
    """run-learn-cycle: ensure backlog_tickets_synced when the agent omitted it."""
    if step_id != "run-learn-cycle" or status != "completed":
        return outputs
    out = dict(outputs)
    if "backlog_tickets_synced" in out:
        return out
    out["backlog_tickets_synced"] = []
    sys.stderr.write(
        "[record] supplemented outputs.backlog_tickets_synced=[] for "
        "run-learn-cycle (COMPLETION missing backlog sync list)\n"
    )
    return out


def _merge_evidence_block(
    outputs: dict[str, Any],
    raw_evidence: Any,
) -> dict[str, Any]:
    """Build step_history evidence from payload outputs + optional evidence block.

    Drivers sometimes emit ``evidence`` as a YAML list of command records;
    spreading that dict would raise TypeError. Lists are stored under
    ``evidence.commands``; mappings are merged with payload outputs winning on
    key overlap.
    """
    if raw_evidence is None:
        return {"outputs": outputs}
    if isinstance(raw_evidence, dict):
        merged = dict(raw_evidence)
        prior = merged.get("outputs")
        if isinstance(prior, dict):
            merged["outputs"] = {**prior, **outputs}
        else:
            merged["outputs"] = outputs
        return merged
    if isinstance(raw_evidence, list):
        sys.stderr.write(
            "[record] warning: evidence must be a mapping; "
            "storing list under evidence.commands\n"
        )
        return {"outputs": outputs, "commands": raw_evidence}
    return {"outputs": outputs, "detail": raw_evidence}
