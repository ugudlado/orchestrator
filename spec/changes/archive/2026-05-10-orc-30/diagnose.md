# ORC-30 Diagnosis: Pricing Lookup Miss for Dated Model IDs

**Change ID**: orc-30  
**Phase**: main  
**Step**: diagnose  
**Date**: 2026-05-10

---

## Symptom

When Anthropic returns a model ID with a date suffix (e.g. `claude-sonnet-4-6-20251001`),
the DuckDB pricing lookup finds no exact match, silently falls back to the `__default__`
row (opus-tier rates), and records cost_usd at 5× the correct rate for sonnet and up to
18.75× the correct rate for haiku.

---

## Preliminary Note: Stale Context in the Task Description

The ORC-30 backlog task describes the bug in terms of `config/pricing.yaml` and suggests
a fix using `pricing.get("models")`. That file was **deleted** in commit `3caccfd` as part
of the `pricing-table-in-duckdb` feature — DuckDB is now the single source of truth.
The prototype fix in the task description targets code that no longer exists. The actual
fix lives in `_lookup_price` inside `record.py`.

---

## Reproduction

Reproduce from the repo root:

```
python .tmp/repro_orc30.py
```

Script location: `/Users/spidey/code/orchestrator/.tmp/repro_orc30.py`

### Evidence output (confirmed 2026-05-10):

```
=== ORC-30: Pricing Lookup Bug Reproduction ===

Seeded model IDs in DuckDB:
  __default__
  claude-haiku-4-5
  claude-haiku-4-5-20251001
  claude-opus-4-5
  claude-opus-4-6
  claude-opus-4-7
  claude-sonnet-4-5
  claude-sonnet-4-6
  coder
  qwen/qwen3-coder-30b-a3b-instruct

claude-haiku-4-5-20251001 (SEEDED):   input=$0.8/MTok  [CORRECT — seeded explicitly]
claude-sonnet-4-6-20251001 (NOT seeded): input=$15.0/MTok  [BUG: should be $3.0, overstate=5.0x]
claude-haiku-4-5-20260101 (NOT seeded): input=$15.0/MTok  [BUG: should be $0.8, overstate=18.8x]

__default__ rates: input=$15.00/MTok, output=$75.00/MTok

VERDICT: Any dated model ID not explicitly present in pricing table uses __default__ (opus-tier) rates.
```

**Correction to the task description**: The task claimed ~4× for haiku and ~5× for sonnet.
The actual overstatement for haiku is 18.75× (input: $0.80 → $15.00) and for sonnet is 5.0×
(input: $3.00 → $15.00). The task's numbers may have assumed a different token mix weighting
cache vs. non-cache tokens.

---

## Root Cause

### Location

File: `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`

**`_lookup_price` — lines 444–455:**
```python
def _pick_row(mid: str):
    """Return the most-recent row for mid with effective_from <= effective_at."""
    candidates = by_model.get(mid, [])   # ← LINE 446: exact key match only
    for eff, inp, out, cr, cc in candidates:
        if eff <= effective_at:
            return (inp, out, cr, cc)
    return None

row = _pick_row(model_id)                # ← LINE 453: first try exact dated key
if row is None:
    row = _pick_row("__default__")       # ← LINE 454-455: miss → __default__ (opus-tier)
```

The function calls `by_model.get(mid, [])` which is a dictionary key lookup. The in-process
cache (`_pricing_cache`) is populated from the `pricing` DuckDB table at line 409:
```python
rows = db.execute(_LOAD_ALL_SQL).fetchall()
for row in rows:
    mid, eff, inp, out, cr, cc = row
    by_model.setdefault(mid, []).append((eff, inp, out, cr, cc))
```

The table contains unstamped keys (`claude-sonnet-4-6`, `claude-haiku-4-5`). When
`_aggregate()` in `jsonl_usage.py` (line 82–84) reads the JSONL, it records the model
string **exactly as returned by the Anthropic API** — which includes the date suffix:
```
"model": "claude-haiku-4-5-20251001"
```

This dated string is then stored in `usage["model"]` and passed directly to `_lookup_price`
at record.py line 544. There is **no date-suffix normalization** between JSONL parse and
lookup, so `by_model.get("claude-sonnet-4-6-20251001", [])` returns `[]` and the lookup
falls through to `__default__`.

### Divergence point: record.py line 453

`row = _pick_row(model_id)` where `model_id = "claude-sonnet-4-6-20251001"` returns `None`
because the pricing cache only has the key `"claude-sonnet-4-6"`. Execution jumps to the
`__default__` fallback at line 455, which uses opus-tier rates.

---

## Prior Partial Mitigation

Commit `190df05` ("improve: per-tool wall-clock duration + haiku-date pricing alias",
2026-04-19) added an explicit alias to `config/pricing.yaml` for `claude-haiku-4-5-20251001`
and this was later carried into `0001_seed_pricing.sql` line 27. The commit message noted:

> "List the alias explicitly so the lookup matches without a regex stripper. Add new dated
> aliases here as they appear."

This means the explicit-alias strategy was deliberately chosen once before. However, it
is a manual per-release maintenance obligation: any new dated suffix that appears in
production JONLs before an alias is added will silently inflate costs.

---

## Current Real-World Blast Radius

Grep of `~/.claude/projects/-Users-spidey-code-orchestrator/**/*.jsonl` for dated model IDs:

```
1460  "model":"claude-haiku-4-5-20251001"   ← seeded; currently correct
    0  any other dated model ID
```

The only dated ID currently flowing through this repo's JONLs is `claude-haiku-4-5-20251001`,
which is explicitly seeded. **No active cost overstatement is occurring for this repo
right now.** The bug is latent: it will trigger when Anthropic rotates to a new date suffix
or introduces a dated sonnet/opus variant that has not been added to the seed migration.

---

## Impact

**Call sites of `_compute_cost_usd` in record.py** (all three paths are affected equally):

| Line | Context |
|------|---------|
| 172  | `_resolve_driver_session` — driver-loop cost |
| 335  | `_write_subagent_events` — per-subagent cost |
| 1205 | `record()` — main step event cost |

All three call sites resolve `model_id` through `usage.get("model")` (JSONL billing truth,
step 0 of `_compute_cost_usd`) and then call `_lookup_price(db, model_id, effective_at)`.
None of them strip the date suffix before the lookup.

**Affected dimensions when bug is active:**
- `cost_usd` column in `step_events` table is overstated
- `cost_usd` column in `driver_sessions` table is overstated
- `cost_usd` in `phase_events` (aggregated from step_events) is overstated
- Cost reports (`scripts/cost-report.sh`) that read from these tables show inflated figures
- Baseline comparisons using `median_cost_usd` in `feature_metrics` are skewed

**No data loss or incorrect behavior beyond cost figures.**

---

## Proposed Approach

Two viable strategies exist; selection is deferred to the architect:

**Strategy A (Regex normalization)**: Strip the date suffix at lookup time inside
`_lookup_price` — try `model_id` as-is, then strip `r'-\d{8}$'` and retry before falling
back to `__default__`. Self-maintaining for all future dated IDs of known models; loses
ability to price a specific dated variant at different rates from its base model.

**Strategy B (Explicit alias enumeration)**: Add all known dated aliases to the seed
migration `0001_seed_pricing.sql` (continuing the approach in commit `190df05`). Requires
manual maintenance when Anthropic rotates date suffixes; allows per-dated-version pricing
if Anthropic ever changes rates mid-generation.

---

## Unresolved Questions

1. **Does Anthropic ever price a dated variant differently from its base model?** If yes,
   Strategy A (regex strip) would silently apply the wrong rates. If no, it is safe.

2. **Are dated IDs returned for opus and sonnet variants in other deployments or repos?**
   The JSONL survey above covers only this repo. The fix should be validated against any
   shared `metrics.duckdb` that may have received inflated rows before this bug is caught.

3. **Should existing inflated `cost_usd` rows be backfilled?** Out of scope for the fix
   itself but relevant for reporting accuracy.
