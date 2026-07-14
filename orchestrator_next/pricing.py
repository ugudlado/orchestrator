"""Shared pricing logic for the orchestrator.

Reads from config/pricing.yaml (static file) instead of DuckDB.
"""
from __future__ import annotations

import datetime as _dt
import functools
import re
import sys

import yaml


# ---------------------------------------------------------------------------
# File-based pricing table
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_pricing_table() -> dict[str, list]:
    """Load config/pricing.yaml and return rows keyed by model_id.

    Each value is a list of (effective_from_dt, input, output, cache_read, cache_creation)
    tuples sorted descending by effective_from so _pick_row can do a simple linear scan.
    """
    from orchestrator_next.paths import ConfigRootError, config_root
    try:
        path = config_root() / "pricing.yaml"
    except ConfigRootError as exc:
        sys.stderr.write(f"[record] pricing: {exc}\n")
        return {}
    if not path.is_file():
        sys.stderr.write(f"[record] pricing: config/pricing.yaml not found at {path}\n")
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        sys.stderr.write(f"[record] pricing: failed to load pricing.yaml: {exc}\n")
        return {}

    by_model: dict[str, list] = {}
    for row in data.get("models") or []:
        mid = row.get("model_id")
        if not mid:
            continue
        eff_raw = row.get("effective_from", "2000-01-01T00:00:00")
        try:
            eff = _dt.datetime.fromisoformat(str(eff_raw))
        except (ValueError, TypeError):
            eff = _dt.datetime(2000, 1, 1)
        by_model.setdefault(mid, []).append((
            eff,
            float(row.get("input_usd") or 0),
            float(row.get("output_usd") or 0),
            float(row.get("cache_read_usd") or 0),
            float(row.get("cache_creation_usd") or 0),
        ))

    # Sort each model's rows descending by effective_from
    for mid in by_model:
        by_model[mid].sort(key=lambda r: r[0], reverse=True)

    return by_model


_DATED_MODEL_SUFFIX_RE = re.compile(r"-\d{8}$")


def _lookup_price(model_id: str, effective_at: "_dt.datetime") -> dict | None:
    """Look up pricing rates for model_id from config/pricing.yaml.

    Returns a dict with keys: input, output, cache_read, cache_creation (float, $/MTok).
    Returns None if model_id has no row — an unpriced model records no cost rather
    than borrowing another model's rates, which would invent a confident wrong number.
    """
    by_model = _load_pricing_table()

    def _pick_row(mid: str):
        for eff, inp, out, cr, cc in by_model.get(mid, []):
            if eff <= effective_at:
                return (inp, out, cr, cc)
        return None

    row = _pick_row(model_id)
    if row is None:
        base = _DATED_MODEL_SUFFIX_RE.sub("", model_id)
        if base != model_id:
            row = _pick_row(base)
    if row is None:
        sys.stderr.write(
            f"[record] pricing: no price entry for model {model_id!r}; "
            f"recording no cost. Add a row to config/pricing.yaml to price it.\n"
        )
        return None

    inp, out, cr, cc = row
    return {
        "input": float(inp),
        "output": float(out),
        "cache_read": float(cr),
        "cache_creation": float(cc),
    }


def _billable_token_units(usage: dict | None) -> int:
    if not isinstance(usage, dict):
        return 0
    return int(
        (usage.get("input_tokens") or 0)
        + (usage.get("output_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
    )


def _compute_cost_usd(
    agent: str, usage: dict, *, now: "_dt.datetime | None" = None
) -> tuple[str | None, float | None]:
    """Compute cost from usage.model + token counts via config/pricing.yaml.

    Returns (model_id, cost_usd) or (model_id, None) if pricing unavailable,
    or (None, None) if usage carries no model.
    """
    model_id: str | None = usage.get("model") if isinstance(usage, dict) else None

    if not model_id:
        bills = _billable_token_units(usage)
        if bills > 0:
            sys.stderr.write(
                f"[record] cost_usd: agent {agent!r} billed {bills} tokens but no "
                f"model_id resolved; recording no cost\n"
            )
        elif agent not in ("inline", None):
            sys.stderr.write(
                f"[record] cost_usd: agent {agent!r} has no usage.model set; "
                f"skipping cost computation\n"
            )
        return None, None

    effective_at = now if now is not None else _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    price = _lookup_price(model_id, effective_at)
    if price is None:
        return model_id, None

    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_read_tokens = usage.get("cache_read_input_tokens") or 0
    cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0
    cost = (
        input_tokens * price["input"] / 1_000_000
        + output_tokens * price["output"] / 1_000_000
        + cache_read_tokens * price["cache_read"] / 1_000_000
        + cache_creation_tokens * price["cache_creation"] / 1_000_000
    )
    return model_id, cost


# ---------------------------------------------------------------------------
# Running cost total (re-derived from live state.yaml — no DuckDB)
# ---------------------------------------------------------------------------

def sum_cost_usd(state: dict) -> float:
    """Sum step_history[].usage.cost_usd from an in-memory state dict.

    Pure and dependency-free: iterates the recorded step_history and adds each
    entry's usage.cost_usd defensively. Missing keys, None, and non-numeric
    values contribute 0.0 rather than raising, so a partially-populated state
    still yields a usable running total mid-run.
    """
    total = 0.0
    for entry in state.get("step_history", []) or []:
        if not isinstance(entry, dict):
            continue
        usage = entry.get("usage")
        if not isinstance(usage, dict):
            continue
        cost = usage.get("cost_usd", 0) or 0
        try:
            total += float(cost)
        except (TypeError, ValueError):
            continue
    return total


def format_cost_so_far(state: dict) -> str:
    """Render the human-readable running-total line, e.g. `[cost so far: $12.34]`."""
    return f"[cost so far: ${sum_cost_usd(state):.2f}]"


def format_last_step_usage(state: dict) -> str:
    """Render the most-recent step's own usage, e.g.
    `9.3s  in=12.1k out=834 cache_r=88.2k cache_w=1.2k  $0.69`.

    Returns "" when the last step recorded no usage at all (nothing worth a line).
    Token key names match what the usage adapters write and pricing reads —
    cache_read_input_tokens / cache_creation_input_tokens.
    """
    history = state.get("step_history") or []
    if not history or not isinstance(history[-1], dict):
        return ""
    usage = history[-1].get("usage")
    if not isinstance(usage, dict):
        return ""

    def _n(key: str) -> int:
        try:
            return int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    def _k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    parts = []
    duration_ms = _n("duration_ms")
    if duration_ms >= 100:  # sub-100ms script steps would just render "0.0s"
        parts.append(f"{duration_ms / 1000:.1f}s")

    tokens = [
        ("in", _n("input_tokens")),
        ("out", _n("output_tokens")),
        ("cache_r", _n("cache_read_input_tokens")),
        ("cache_w", _n("cache_creation_input_tokens")),
    ]
    if any(n for _, n in tokens):
        parts.append(" ".join(f"{label}={_k(n)}" for label, n in tokens))

    cost = usage.get("cost_usd") or 0
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        cost = 0.0
    if cost:
        parts.append(f"${cost:.2f}")

    return "  ".join(parts)
