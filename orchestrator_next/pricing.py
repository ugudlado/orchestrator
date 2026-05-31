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
    """Load config/agents.yaml routing config (cached per process).

    ORC-105: merged from scripts/routes.yaml into config/agents.yaml. Falls
    back to the legacy path so older installs / worktrees still resolve.
    """
    from orchestrator_next.paths import config_root
    path = config_root() / "agents.yaml"
    if not path.is_file():
        path = _orchestrator_home() / "scripts" / "routes.yaml"  # legacy fallback
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


def _model_id_from_route(routes: dict, route_entry: object) -> str | None:
    """Map an agents.<name> route entry to a concrete model_id."""
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
        route_entry = (routes.get("agents") or {}).get(agent)
        if route_entry:
            model_id = _model_id_from_route(routes, route_entry)
            if not model_id and bills == 0:
                sys.stderr.write(
                    f"[record] cost_usd: route {route_entry!r} for agent {agent!r} "
                    f"not resolved to a model_id; skipping cost computation\n"
                )

        # Step 2c: token-backed fallback — inline / driver-loop / unknown agents
        # often have JSONL totals but no model string; price via __default__ row.
        if not model_id and bills > 0:
            model_id = "__default__"

        if not model_id:
            if not route_entry and agent not in ("inline", None):
                sys.stderr.write(
                    f"[record] cost_usd: agent {agent!r} not in routes.yaml and "
                    f"usage.model not set; skipping cost computation\n"
                )
            return None, None

    # Step 3: look up price from DuckDB (with __default__ fallback inside _lookup_price)
    # Naive UTC instant — kept tz-naive to match the DuckDB pricing table's
    # effective_from column (DuckDB TIMESTAMP rows come back tz-naive).
    effective_at = now if now is not None else _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
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


# ---------------------------------------------------------------------------
# Bulk pricing CLI (ORC-71, Decisions D-2/D-5/D-6)
# ---------------------------------------------------------------------------
# `python3 -m orchestrator_next.pricing --agents a b c …` prices the
# caller-supplied agents in one process and prints a JSON array. It is a pure
# pricer — it does NOT discover or enumerate agents; agent-list assembly is the
# caller's responsibility (estimate-cost.sh owns the routes ∪ archive union).


def _resolve_agent_model(agent: str) -> "tuple[str | None, str | None]":
    """Resolve agent → (backend, model_id) via routes.yaml.

    Uses the same agent→backend→model chain as `_compute_cost_usd` Steps 1-2:
      - routes.agents[agent] → backend
      - routes.backends[backend] → model_id   (native_* keys)
      - routes.models[backend].model → model_id  (proxy path)
    Returns (None, None) for an agent absent from routes.yaml or a backend that
    resolves to no model_id — the caller then prices it via the __default__ row.
    """
    routes = _load_routes()
    route_entry = (routes.get("agents") or {}).get(agent)
    if not route_entry:
        return None, None
    return _route_backend_label(route_entry), _model_id_from_route(routes, route_entry)


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point: price a caller-supplied list of agents.

    Parses `--agents <name> …` (required, non-empty). Resolves the metrics DB
    via $METRICS_DB else the CLI install location. Prints a JSON array
    of one object per agent to stdout, with keys: agent, backend, model,
    input_usd, output_usd, cache_read_usd, cache_creation_usd.

    Exit codes:
      0  — priced successfully, JSON array on stdout.
      2  — usage error (missing/empty --agents): nothing on stdout.
      1  — metrics DB absent (D-2): stderr diagnostic, nothing on stdout.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python3 -m orchestrator_next.pricing",
        description="Bulk-price caller-supplied agents against the DuckDB pricing table.",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        required=True,
        metavar="AGENT",
        help="one or more agent names to price (required, non-empty)",
    )
    # argparse exits 2 with a usage error on stderr for a missing/empty --agents.
    args = parser.parse_args(argv)

    # Resolve the metrics DB path — engine state, pinned to the CLI location
    # (METRICS_DB override), independent of where the workflow config lives.
    from orchestrator_next.paths import metrics_db_path
    db_path = metrics_db_path()

    # D-2: DB absent → fail loud. No fabricated rates, no stdout.
    if not db_path.exists():
        sys.stderr.write(
            f"[pricing] metrics DB not found at {db_path}; cannot price agents\n"
        )
        return 1

    import duckdb

    db = duckdb.connect(str(db_path), read_only=True)
    try:
        # Naive UTC instant — kept tz-naive to match the DuckDB pricing table's
        # effective_from column (DuckDB TIMESTAMP rows come back tz-naive).
        now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
        results = []
        for agent in args.agents:
            backend, model_id = _resolve_agent_model(agent)
            # Unrouted agent → price via the __default__ pricing row; report
            # backend/model as null (consistent with _compute_cost_usd handling
            # of an unrouted agent).
            lookup_model = model_id if model_id else "__default__"
            price = _lookup_price(db, lookup_model, now)
            if price is None:
                # No row for the model and no __default__ row — emit nulls
                # rather than guessing (D-2 fail-loud spirit).
                results.append({
                    "agent": agent,
                    "backend": backend,
                    "model": model_id,
                    "input_usd": None,
                    "output_usd": None,
                    "cache_read_usd": None,
                    "cache_creation_usd": None,
                })
                continue
            results.append({
                "agent": agent,
                "backend": backend,
                "model": model_id,
                "input_usd": price["input"],
                "output_usd": price["output"],
                "cache_read_usd": price["cache_read"],
                "cache_creation_usd": price["cache_creation"],
            })
    finally:
        db.close()

    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
