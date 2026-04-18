"""
Short → OTel column-name mapping and JSON serialisation utilities.

Short names used in state.yaml step_history[].usage:
  input_tokens              → gen_ai_usage_input_tokens
  output_tokens             → gen_ai_usage_output_tokens
  cache_read_input_tokens   → gen_ai_usage_cache_read_input_tokens
  cost_usd                  → gen_ai_usage_cost_usd
  model                     → gen_ai_request_model
  duration_ms               → duration_ms  (same name)
  tool_calls                → tool_calls_json (JSON-serialised dict)

All other keys in the usage block are silently ignored (forward-compatible).
"""
from __future__ import annotations

import json
from typing import Any

# Scalar field mappings: short_name → otel_column_name
_SCALAR_MAP: dict[str, str] = {
    "input_tokens": "gen_ai_usage_input_tokens",
    "output_tokens": "gen_ai_usage_output_tokens",
    "cache_read_input_tokens": "gen_ai_usage_cache_read_input_tokens",
    "cost_usd": "gen_ai_usage_cost_usd",
    "model": "gen_ai_request_model",
    "duration_ms": "duration_ms",
}

# Fields that require JSON serialisation (dict → JSON string)
_JSON_FIELDS: dict[str, str] = {
    "tool_calls": "tool_calls_json",
}


def usage_to_otel(usage: dict[str, Any]) -> dict[str, Any]:
    """
    Map a step_history[].usage dict to OTel column names.

    Returns a flat dict keyed by OTel column names. Fields not present in the
    usage block are absent from the output (caller is responsible for defaults).
    """
    result: dict[str, Any] = {}

    for short_name, otel_col in _SCALAR_MAP.items():
        if short_name in usage:
            result[otel_col] = usage[short_name]

    for short_name, otel_col in _JSON_FIELDS.items():
        if short_name in usage:
            val = usage[short_name]
            result[otel_col] = json.dumps(val, sort_keys=True) if val is not None else None

    return result


def serialise_artifacts(artifacts: list[Any] | None) -> str | None:
    """Serialise artifacts list to a JSON string, or None if absent/empty."""
    if not artifacts:
        return None
    return json.dumps(artifacts)


def serialise_escalation(escalation: dict[str, Any] | None) -> str | None:
    """Serialise escalation sub-block to a JSON string, or None if absent."""
    if not escalation:
        return None
    return json.dumps(escalation, sort_keys=True)
