"""Shared pricing logic for the orchestrator (ORC-71).

Single owner of routes resolution and DuckDB pricing lookup. Both consumers go
through this module: `record.py` by in-process import (re-exporting these
symbols by reference), and `estimate-cost.sh` by subprocess via the CLI entry
point.

Logic here is lifted verbatim (byte-equivalent) from `record.py`'s former
pricing helpers (record.py:580-789, ISSUE-17). The `[record]` stderr warning
prefixes are kept unchanged: no test pins the pricing-function prefix, and
keeping it is the minimum-diff, behavior-preserving choice (Decision D-8).
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
# Cost computation helpers (ISSUE-17)
# ---------------------------------------------------------------------------

def _orchestrator_home() -> Path:
    """Return the orchestrator repo root.

    Prefers ORCHESTRATOR_HOME env var; falls back to the parent of the
    `config/scripts/` tree this module lives in.
    """
    env = os.environ.get("ORCHESTRATOR_HOME")
    if env:
        return Path(env)
    # __file__ is config/scripts/orchestrator_next/pricing.py
    return Path(__file__).parent.parent.parent.parent


@functools.lru_cache(maxsize=1)
def _load_routes() -> dict:
    """Load scripts/routes.yaml (cached per process)."""
    path = _orchestrator_home() / "scripts" / "routes.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


_LOOKUP_SQL = (
    "SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd "
    "FROM pricing "
    "WHERE model_id = ? AND effective_from <= ? "
    "ORDER BY effective_from DESC LIMIT 1"
)

# Per-connection pricing row cache keyed by id(db).
# Populated on first lookup; stores ALL rows so subsequent lookups are pure-Python
# dict accesses (NFR-1: 1000 calls < 50 ms).
# Shape: {db_key: {model_id: [(effective_from, input, output, cache_read, cache_creation), ...]}}
# Rows are stored sorted descending by effective_from so we can do a linear scan.
_pricing_cache: dict[int, dict[str, list]] = {}

_LOAD_ALL_SQL = (
    "SELECT model_id, effective_from, input_usd, output_usd, "
    "cache_read_usd, cache_creation_usd "
    "FROM pricing ORDER BY model_id, effective_from DESC"
)


def _ensure_pricing_cache(db) -> dict[str, list]:
    """Load all pricing rows into the in-process cache for this connection."""
    db_key = id(db)
    if db_key in _pricing_cache:
        return _pricing_cache[db_key]
    rows = db.execute(_LOAD_ALL_SQL).fetchall()
    by_model: dict[str, list] = {}
    for row in rows:
        mid, eff, inp, out, cr, cc = row
        by_model.setdefault(mid, []).append((eff, inp, out, cr, cc))
    _pricing_cache[db_key] = by_model
    return by_model


_DATED_MODEL_SUFFIX_RE = re.compile(r"-\d{8}$")


def _lookup_price(db, model_id: str, effective_at: "_dt.datetime") -> dict | None:
    """Look up pricing rates for model_id at effective_at from the DuckDB pricing table.

    Returns a dict with keys: input, output, cache_read, cache_creation (float, $/MTok).
    Returns None (with a stderr warning) if:
      - db is None (offline/test path, FR-3(a))
      - no row exists for model_id AND no __default__ row exists

    The __default__ fallback is transparent — no warning is emitted on that path.

    Performance: all pricing rows are loaded into an in-process cache on the first
    call per connection so subsequent calls are pure-Python dict lookups (NFR-1).

    Args:
        db: open DuckDB connection, or None for the offline path.
        model_id: model identifier to look up.
        effective_at: datetime; the most recent row with effective_from <= this is used.
    """
    if db is None:
        sys.stderr.write(
            f"[record] pricing: db=None; skipping price lookup for {model_id!r}\n"
        )
        return None

    by_model = _ensure_pricing_cache(db)

    def _pick_row(mid: str):
        """Return the most-recent row for mid with effective_from <= effective_at."""
        candidates = by_model.get(mid, [])
        # Rows are pre-sorted descending by effective_from.
        for eff, inp, out, cr, cc in candidates:
            if eff <= effective_at:
                return (inp, out, cr, cc)
        return None

    row = _pick_row(model_id)
    if row is None:
        # ORC-30: dated model ID (e.g. claude-sonnet-4-6-20260315) → try base.
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
        "cache_creation": float(cc) if cc is not None else 0.0,
    }


def _billable_token_units(usage: dict | None) -> int:
    """Sum of input/output/cache token counts for cost eligibility (ISSUE-inline-cost)."""
    if not isinstance(usage, dict):
        return 0
    return int(
        (usage.get("input_tokens") or 0)
        + (usage.get("output_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
    )


def _compute_cost_usd(
    db, agent: str, usage: dict, *, now: "_dt.datetime | None" = None
) -> tuple[str | None, float | None]:
    """Resolve agent → model_id and compute cost from usage token counts via DuckDB.

    Resolution order:
      0. usage['model']  — billing-truth from JSONL, always preferred.
      1. routes.agents[agent] → backend_name
      2a. routes.backends[backend_name] → model_id  (for native_* keys)
      2b. routes.models[backend_name].model → model_id  (for proxy models)
      2c. If still unresolved but billable tokens > 0, use model_id '__default__'
          so DuckDB pricing applies (same table row _lookup_price already falls back to).
    Price lookup: _lookup_price(db, model_id, now) with __default__ fallback.

    Returns (model_id, cost_usd) or (model_id, None) if pricing unavailable,
    or (None, None) if model resolution fails.
    Missing token fields default to 0.
    """
    routes = _load_routes()

    # Step 0: prefer usage.model when present (JSONL-sourced billing truth).
    # This path lets synthetic rows (driver-loop) compute cost without being
    # registered in routes.yaml.
    model_id: str | None = usage.get("model") if isinstance(usage, dict) else None
    bills = _billable_token_units(usage)

    if not model_id:
        # Step 1: agent → backend
        backend = (routes.get("agents") or {}).get(agent)
        if backend:
            # Step 2: backend → model_id
            backends_map = routes.get("backends") or {}
            if backend in backends_map:
                model_id = backends_map[backend]
            else:
                # Try routes.models.<backend>.model (proxy path)
                model_entry = (routes.get("models") or {}).get(backend)
                if isinstance(model_entry, dict):
                    model_id = model_entry.get("model")
            if not model_id and bills == 0:
                sys.stderr.write(
                    f"[record] cost_usd: backend {backend!r} for agent {agent!r} "
                    f"not resolved to a model_id; skipping cost computation\n"
                )

        # Step 2c: token-backed fallback — inline / driver-loop / unknown agents
        # often have JSONL totals but no model string; price via __default__ row.
        if not model_id and bills > 0:
            model_id = "__default__"

        if not model_id:
            if not backend:
                sys.stderr.write(
                    f"[record] cost_usd: agent {agent!r} not in routes.yaml and "
                    f"usage.model not set; skipping cost computation\n"
                )
            return None, None

    # Step 3: look up price from DuckDB (with __default__ fallback inside _lookup_price)
    effective_at = now if now is not None else _dt.datetime.utcnow()
    price = _lookup_price(db, model_id, effective_at)
    if price is None:
        return model_id, None

    # Step 4: compute cost
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
