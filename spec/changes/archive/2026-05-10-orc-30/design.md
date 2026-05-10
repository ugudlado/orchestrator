# ORC-30 Design — Date-Suffix Strip in `_lookup_price`

**Change ID**: orc-30
**Workflow**: bugfix
**Date**: 2026-05-10

---

## Selected Approach: Regex Strip on Cache Miss (Approach A)

After an exact-key cache lookup misses, strip a trailing `-YYYYMMDD` suffix from
the model ID and retry the cache lookup once. If that still misses, fall through
to the existing `__default__` path unchanged.

### Why this approach

- **Self-maintaining**: every future dated variant of an already-seeded base model
  is priced correctly with no migration or code change.
- **Preserves per-variant granularity**: exact match is attempted first, so an
  explicit dated row in `0001_seed_pricing.sql` (e.g. the existing
  `claude-haiku-4-5-20251001`) still wins over the stripped-base lookup.
- **Defensive, minimal**: one regex, one retry, contained in a single function.
  No new modules, no schema work, no behavior change for non-dated IDs.
- **Single point of impact**: all three call sites of `_compute_cost_usd`
  (`record.py:172`, `:335`, `:1205`) benefit transparently.

---

## Code Location

**File**: `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`
**Function**: `_lookup_price` (definition starting near line 416, body ~444–461)

The change is confined to the lookup ladder between `_pick_row(model_id)` and the
`__default__` fallback. The `_pick_row` inner closure, the `_ensure_pricing_cache`
call, the cache structure, and the return shape are all unchanged.

---

## Implementation

### Module-level regex (compile once)

Add near the top of `record.py` with the other module-level constants:

```python
import re

# ORC-30: Anthropic API returns model IDs with a trailing -YYYYMMDD date stamp.
# When the dated variant is not explicitly seeded in the pricing table, strip
# the suffix and look up the base model.
_DATED_MODEL_SUFFIX_RE = re.compile(r"-\d{8}$")
```

### Lookup ladder

Modify the lookup block in `_lookup_price` (currently lines ~453–455):

```python
# Before
row = _pick_row(model_id)
if row is None:
    row = _pick_row("__default__")
```

```python
# After
row = _pick_row(model_id)
if row is None:
    # ORC-30: dated model ID (e.g. claude-sonnet-4-6-20260315) → try base.
    base = _DATED_MODEL_SUFFIX_RE.sub("", model_id)
    if base != model_id:
        row = _pick_row(base)
if row is None:
    row = _pick_row("__default__")
```

The `base != model_id` guard avoids a redundant second lookup when the model ID
has no date suffix (the regex didn't match).

### What does NOT change

- `_ensure_pricing_cache` / `_pricing_cache` / `_LOAD_ALL_SQL` — pricing cache
  loading is untouched.
- `_pick_row` inner closure — same behavior.
- Stderr warning on total miss — same wording, same trigger condition (no exact,
  no base, no `__default__`).
- Return shape — same dict with `input`, `output`, `cache_read`, `cache_creation`.
- All three call sites of `_compute_cost_usd` — no edits.

---

## Data Flow

```
JSONL parse (jsonl_usage.py)
  usage["model"] = "claude-sonnet-4-6-20260315"     ← from Anthropic API
        │
        ▼
_compute_cost_usd(db, agent, usage, now=...)
  model_id = usage["model"]                          ← billing truth (step 0)
        │
        ▼
_lookup_price(db, model_id, effective_at)
  ├── _pick_row("claude-sonnet-4-6-20260315") → None     [exact miss]
  ├── base = "claude-sonnet-4-6"                          [NEW: strip -\d{8}$]
  ├── _pick_row("claude-sonnet-4-6")        → row        [NEW: base hit]
  └── return row                                          [correct sonnet rates]

Fallback chain on total miss:
  exact → base (if dated) → __default__ → None+stderr warning
```

---

## Error Handling

- Non-dated model ID with no seeded row: regex `sub` returns the input unchanged,
  `base != model_id` is `False`, second lookup skipped, falls to `__default__`.
  **Behavior identical to today.**
- Dated model ID where neither dated nor base are seeded: both `_pick_row` calls
  return `None`, falls to `__default__`. **Behavior identical to today** (still
  warns via the existing `__default__`-miss stderr line if `__default__` is also
  missing).
- `db is None`: early-return path at `_lookup_price` line 436 is untouched.

---

## Testing Strategy

A new regression test file `tests/test_pricing_lookup_dated.py` covers AC-1
through AC-4. The existing `tests/test_pricing_lookup.py` (six scenarios incl.
the micro-benchmark) is run unchanged to satisfy AC-6.

Test scenarios:

1. **AC-1**: Seed `claude-sonnet-4-6` only; look up
   `claude-sonnet-4-6-20260315`; expect sonnet rates (not `__default__`).
2. **AC-2**: Seed both `claude-haiku-4-5` (haiku rates) and
   `claude-haiku-4-5-20251001` (a deliberately distinct rate to prove which row
   was returned); look up the dated form; expect the dated row, **not** the
   base — exact match wins.
3. **AC-3**: Seed `__default__` only; look up `claude-future-99` (no date
   suffix); expect `__default__` rates (regex doesn't match, no extra lookup).
4. **AC-4**: Seed `__default__` only; look up `unknown-model-20260101`; expect
   `__default__` rates (base also absent).

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Anthropic prices a dated variant differently from its base (diagnose Q1). | Exact match is tried first. To pin a different rate, add an explicit row in `0001_seed_pricing.sql` — same mechanism as the existing `claude-haiku-4-5-20251001` row. |
| Regex matches a non-date 8-digit suffix on some unrelated model name. | The pattern is anchored (`$`) and requires exactly 8 digits preceded by `-`. No current or known future Anthropic / proxy model name uses this shape for non-date purposes. If a collision ever appears, an explicit pricing row overrides via the exact-match-first rule. |
| Performance regression. | One additional `re.sub` and at most one extra dict lookup on miss. The compiled regex is module-level. NFR-3 micro-benchmark (1000 calls < 50 ms) re-runs in CI. |

---

## Files Touched

- `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`
  — add `_DATED_MODEL_SUFFIX_RE` module constant; insert ~3 lines into the
  lookup ladder of `_lookup_price`.
- `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/tests/test_pricing_lookup_dated.py`
  — new file, four scenarios.

No other files. No schema/migration changes.
