# ORC-30 Tasks — Pricing Lookup Date-Suffix Fix

**Change ID**: orc-30
**Workflow**: bugfix (test-first)
**Date**: 2026-05-10

Tasks are ordered: regression test first (RED), then minimal fix (GREEN).
Each task lists files to touch, the exact change, and a verification step.

---

## [x] T-1 — Add regression test for dated-model pricing lookup (RED)

**Goal**: Lock in the four acceptance scenarios before any production code change.
The new test file MUST fail on `main` (proving the bug) and pass after T-2 lands.

**Files**:
- CREATE `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/tests/test_pricing_lookup_dated.py`

**Approach**: Mirror the fixtures and DuckDB-in-memory pattern used by
`tests/test_pricing_lookup.py` (in-memory `duckdb.connect(":memory:")` +
`ensure_schema` + manual INSERTs into the `pricing` table + autouse
`_pricing_cache.clear()` fixture). Add four test functions:

1. `test_dated_id_falls_back_to_base_model_pricing` *[traces: AC-1]*
   - Seed only `claude-sonnet-4-6` (e.g. input=3.0, output=15.0) and `__default__`
     (opus-tier, e.g. input=15.0, output=75.0).
   - Call `_lookup_price(db, "claude-sonnet-4-6-20260315", now)`.
   - Assert the returned `input` is 3.0 (sonnet), NOT 15.0 (`__default__`).

2. `test_exact_dated_match_wins_over_base_strip` *[traces: AC-2]*
   - Seed `claude-haiku-4-5` (e.g. input=0.8) AND `claude-haiku-4-5-20251001`
     with a deliberately distinct sentinel rate (e.g. input=0.7) so the test can
     distinguish which row was returned.
   - Call `_lookup_price(db, "claude-haiku-4-5-20251001", now)`.
   - Assert returned `input` is 0.7 (the exact dated row), NOT 0.8 (the base).

3. `test_non_dated_unknown_model_falls_back_to_default` *[traces: AC-3]*
   - Seed only `__default__`.
   - Call `_lookup_price(db, "claude-future-99", now)`.
   - Assert returned rates equal the `__default__` rates.

4. `test_dated_id_with_unseeded_base_falls_back_to_default` *[traces: AC-4]*
   - Seed only `__default__`.
   - Call `_lookup_price(db, "unknown-model-20260101", now)`.
   - Assert returned rates equal the `__default__` rates.

Reuse the autouse `clear_pricing_cache` fixture pattern from
`test_pricing_lookup.py` to avoid `id(db)` cache reuse across tests.

**Verify**:
```
cd /Users/spidey/code/orchestrator/config/scripts && \
  python -m pytest orchestrator_next/tests/test_pricing_lookup_dated.py -v
```
- Tests 1 and 4 currently MUST fail (proves AC-1 / AC-4 bug exists).
- Tests 2 and 3 may already pass on `main` (current behavior). They guard against
  regression introduced by T-2.

---

## [x] T-2 — Strip `-YYYYMMDD` suffix in `_lookup_price` on cache miss (GREEN)

**Goal**: Make T-1 pass. Minimal, defensive change inside `_lookup_price` only.

**Files**:
- MODIFY `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`

**Approach**:

1. Add a module-level compiled regex near the existing top-of-module imports/
   constants (e.g. just after the other module-level helpers above
   `_lookup_price`):

   ```python
   _DATED_MODEL_SUFFIX_RE = re.compile(r"-\d{8}$")
   ```

   Ensure `import re` is present at module top (add if missing).

2. Inside `_lookup_price`, locate the existing two-line lookup ladder
   (currently approximately lines 453–455):

   ```python
   row = _pick_row(model_id)
   if row is None:
       row = _pick_row("__default__")
   ```

   Replace with:

   ```python
   row = _pick_row(model_id)
   if row is None:
       # ORC-30: dated model ID (e.g. claude-sonnet-4-6-20260315) → try base.
       base = _DATED_MODEL_SUFFIX_RE.sub("", model_id)
       if base != model_id:
           row = _pick_row(base)
   if row is None:
       row = _pick_row("__default__")
   ```

No other edits. Do NOT touch `_compute_cost_usd`, `_ensure_pricing_cache`, or
any of the three call sites — the fix flows through `_lookup_price` only
(satisfies AC-5).

**Verify**:
```
cd /Users/spidey/code/orchestrator/config/scripts && \
  python -m pytest orchestrator_next/tests/test_pricing_lookup_dated.py -v && \
  python -m pytest orchestrator_next/tests/test_pricing_lookup.py -v
```
- All four T-1 scenarios pass *(satisfies AC-1, AC-2, AC-3, AC-4)*.
- All six existing `test_pricing_lookup.py` scenarios still pass, including the
  1000-call < 50 ms micro-benchmark *(satisfies AC-6, NFR-1, NFR-3)*.
- `git diff` shows changes confined to `record.py` (one new constant, ~3 added
  lines in `_lookup_price`) *(satisfies AC-5)*.
