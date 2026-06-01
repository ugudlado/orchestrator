"""Shared pricing logic for the orchestrator.

Reads from config/pricing.yaml (static file) instead of DuckDB.
"""
from __future__ import annotations

import datetime as _dt
import functools
import os
import re
import sys
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Routes resolution
# ---------------------------------------------------------------------------

def _orchestrator_home() -> Path:
    env = os.environ.get("ORCHESTRATOR_HOME")
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent.parent


@functools.lru_cache(maxsize=1)
def _load_routes() -> dict:
    from orchestrator_next.paths import config_root
    path = config_root() / "agents.yaml"
    if not path.is_file():
        path = _orchestrator_home() / "scripts" / "routes.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


def _model_id_from_route(routes: dict, route_entry: object) -> str | None:
    if route_entry is None:
        return None
    if isinstance(route_entry, dict):
        tier = route_entry.get("model")
        if not isinstance(tier, str) or not tier:
            return None
        model_val = (routes.get("models") or {}).get(tier)
        if isinstance(model_val, str):
            return model_val
        if isinstance(model_val, dict):
            mid = model_val.get("model")
            return str(mid) if mid else None
        return None
    backend = route_entry
    backends_map = routes.get("backends") or {}
    if backend in backends_map:
        return backends_map[backend]
    model_val = (routes.get("models") or {}).get(backend)
    if isinstance(model_val, str):
        return model_val
    if isinstance(model_val, dict):
        mid = model_val.get("model")
        return str(mid) if mid else None
    return None


def _route_backend_label(route_entry: object) -> str | None:
    if route_entry is None:
        return None
    if isinstance(route_entry, dict):
        tier = route_entry.get("model")
        return str(tier) if tier else None
    return str(route_entry)


# ---------------------------------------------------------------------------
# File-based pricing table
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_pricing_table() -> dict[str, list]:
    """Load config/pricing.yaml and return rows keyed by model_id.

    Each value is a list of (effective_from_dt, input, output, cache_read, cache_creation)
    tuples sorted descending by effective_from so _pick_row can do a simple linear scan.
    """
    from orchestrator_next.paths import config_root
    path = config_root() / "pricing.yaml"
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
    Returns None if no matching row exists (no __default__ row either).
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
        row = _pick_row("__default__")
    if row is None:
        sys.stderr.write(
            f"[record] pricing: no price entry for model {model_id!r} and no "
            f"__default__ row; skipping cost computation\n"
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
    """Resolve agent → model_id and compute cost from token counts via config/pricing.yaml.

    Returns (model_id, cost_usd) or (model_id, None) if pricing unavailable,
    or (None, None) if model resolution fails.
    """
    routes = _load_routes()

    model_id: str | None = usage.get("model") if isinstance(usage, dict) else None
    bills = _billable_token_units(usage)

    if not model_id:
        route_entry = (routes.get("agents") or {}).get(agent)
        if route_entry:
            model_id = _model_id_from_route(routes, route_entry)
            if not model_id and bills == 0:
                sys.stderr.write(
                    f"[record] cost_usd: route {route_entry!r} for agent {agent!r} "
                    f"not resolved to a model_id; skipping cost computation\n"
                )

        if not model_id and bills > 0:
            model_id = "__default__"

        if not model_id:
            if not route_entry and agent not in ("inline", None):
                sys.stderr.write(
                    f"[record] cost_usd: agent {agent!r} not in routes.yaml and "
                    f"usage.model not set; skipping cost computation\n"
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
