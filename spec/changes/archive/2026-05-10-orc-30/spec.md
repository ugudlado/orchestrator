# ORC-30 Spec — Pricing Lookup for Dated Model IDs

**Change ID**: orc-30
**Workflow**: bugfix
**Phase**: main
**Date**: 2026-05-10

---

## Context

Anthropic API responses include date-suffixed model identifiers (e.g.
`claude-haiku-4-5-20251001`, `claude-sonnet-4-6-20251001`). These IDs are written
verbatim into JSONL `usage.model` and flow into `_compute_cost_usd → _lookup_price`
in `config/scripts/orchestrator_next/record.py`.

`_lookup_price` performs an exact dict-key lookup against the in-process pricing
cache loaded from the DuckDB `pricing` table. The table is seeded from
`0001_seed_pricing.sql` with **base** model keys (`claude-sonnet-4-6`,
`claude-haiku-4-5`, etc.). Any dated variant not explicitly listed misses the cache
and silently falls through to the `__default__` row, which uses opus-tier rates.

Today the only dated ID flowing through this repo is `claude-haiku-4-5-20251001`,
which is explicitly seeded — so no active overstatement is occurring. The bug is
**latent**: the next time Anthropic rotates a date suffix (e.g. a future
`claude-sonnet-4-7-20260315`), cost figures in `step_events`, `driver_sessions`,
`phase_events`, cost reports, and feature-metrics baselines will inflate by
5×–18.75× until a manual seed entry is added.

Diagnosis details: `diagnose.md` (same directory).

---

## Requirements

### Functional

- **FR-1**: When `_lookup_price` is called with a model ID matching the pattern
  `<base>-YYYYMMDD` and `<base>` exists in the pricing table while the dated
  variant does not, the function MUST return the price row for `<base>`.
- **FR-2**: When the dated model ID is itself present in the pricing table (e.g.
  the explicitly-seeded `claude-haiku-4-5-20251001`), the function MUST return that
  exact-match row (preserving per-variant pricing where it exists).
- **FR-3**: When neither the dated ID nor its stripped base form exist, behavior
  MUST be unchanged from today — fall back to `__default__`, then to `None` with
  a stderr warning.
- **FR-4**: The fix MUST live in `_lookup_price` so that **all three** call sites
  of `_compute_cost_usd` (`record.py:172`, `:335`, `:1205`) benefit without code
  duplication.

### Non-functional

- **NFR-1**: No additional DuckDB queries per lookup. The existing in-process
  pricing cache (loaded once per connection) MUST remain the only data source.
- **NFR-2**: No DB schema changes; no new migrations.
- **NFR-3**: The micro-benchmark in `test_pricing_lookup.py` (1000 lookups
  < 50 ms) MUST still pass.

### Out of scope

- Backfilling historical inflated `cost_usd` rows in existing `metrics.duckdb`
  files (tracked separately if needed; see diagnose.md unresolved Q3).
- Per-dated-variant pricing if Anthropic ever rates the same generation
  differently across date stamps (diagnose.md unresolved Q1). Today no such case
  exists; if it appears, an explicit dated row in the seed migration overrides
  the stripped-base lookup via FR-2.
- Changes to `jsonl_usage.py` or any caller — fix is contained in `_lookup_price`.

---

## Acceptance Criteria

- **AC-1**: A new dated model ID whose base is seeded but whose dated form is not
  (e.g. `claude-sonnet-4-6-20260315` against the existing seed) resolves to the
  base model's price tuple, not the `__default__` opus-tier rates.
  *[traces: FR-1]*

- **AC-2**: The explicitly-seeded dated model `claude-haiku-4-5-20251001`
  continues to resolve to its own seeded row (input=$0.80/MTok), not the stripped
  base `claude-haiku-4-5`. Exact-match wins over base-strip fallback.
  *[traces: FR-2]*

- **AC-3**: A model ID with no date suffix and no seeded row (e.g.
  `claude-future-99`) falls back to `__default__` exactly as before.
  *[traces: FR-3]*

- **AC-4**: A model ID whose base is also absent (e.g. `unknown-model-20260101`
  where `unknown-model` is not seeded) falls back to `__default__`.
  *[traces: FR-3]*

- **AC-5**: The fix is implemented inside `_lookup_price` only. No edits to
  `_compute_cost_usd` or its three call sites; all three benefit transparently.
  *[traces: FR-4]*

- **AC-6**: The existing pricing-lookup test suite
  (`tests/test_pricing_lookup.py`, all six existing scenarios incl. the
  micro-benchmark) continues to pass without modification.
  *[traces: NFR-1, NFR-3]*

---

## Alternatives Considered

**Approach B — Continue explicit aliases in seed migration.** Add an INSERT row
per dated suffix as Anthropic releases them. Pros: preserves per-variant pricing
granularity. Cons: every Anthropic date rotation becomes a manual maintenance
event; if a dated ID lands in production JSONL before the seed update is
deployed, costs silently inflate up to 18.75×. Already chosen once (commit
`190df05`) and proven fragile. Rejected on maintenance burden.

**Approach A — Regex strip at lookup time (selected).** In `_lookup_price`,
after an exact-match miss, strip a trailing `-\d{8}` and retry the cache lookup
before falling back to `__default__`. Self-maintaining for all future dated
variants of any seeded base model; preserves per-variant pricing because exact
match is still tried first (FR-2). Complexity: one regex + one retry. No DB
changes, no schema work, no new modules.
