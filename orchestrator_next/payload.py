"""Payload supplement helpers extracted from record.py (ORC-74)."""
from __future__ import annotations

import sys
from typing import Any


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
