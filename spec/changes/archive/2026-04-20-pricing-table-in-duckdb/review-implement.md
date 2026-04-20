# Phase Review: pricing-table-in-duckdb — implement

**Reviewer:** Reviewer Agent (independent verification)
**Date:** 2026-04-21
**Phase:** implement
**Verdict:** REJECTED — score 7.5/10

---

## Verification Results

| Check | Result |
|-------|--------|
| Type-check | Not applicable (Python, no mypy config found) |
| Tests (feature path) | **FAIL** — 1 new failure introduced by feature |
| Build | N/A (no build step) |
| Pre-existing failures | 8 pre-existing failures confirmed identical on main branch |

---

## Acceptance Criteria Assessment

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| AC-1 | `schema_migrations` table created; `0001_seed_pricing.sql` runs once | PASS | `_run_migrations` in upsert.py; idempotent re-run confirmed manually |
| AC-2 | Idempotence: second `ensure_schema` call skips already-applied migrations | PASS | `WHERE filename NOT IN (SELECT filename FROM schema_migrations)` guard |
| AC-3 | Pricing seed: 10 rows (9 models + `__default__`), `effective_from = '2025-01-01T00:00:00'` | PASS | `0001_seed_pricing.sql` — 10 INSERT rows, all `effective_from` match |
| AC-4 | `record.py` calls `_compute_cost_usd(db, agent, usage)` with DB-backed pricing | PASS | `_lookup_price` in record.py uses load-all cache via `_LOOKUP_SQL`; 3 call sites wired |
| AC-5 | `estimate-cost.sh` output byte-equivalent against seeded DuckDB fixture | **FAIL** | `test_rewrite_output_matches_baseline_shape` fails — see Critical Finding C-1 |
| AC-6 | `_load_pricing_for_model(db, model)` replaces YAML loader in `cost_report.py` | PASS | Implemented at cost_report.py line 64; no YAML fallback path |
| AC-7 | No `phase\|feature\|driver` column in pricing DDL; NFR-5 invariant preserved | PASS | `0001_seed_pricing.sql` DDL and INSERTs contain none of these columns |
| AC-8 | `ingest-pricing.py` script: validates, upserts with conflict-skip, `--help` | PASS | Script at `scripts/ingest-pricing.py`; argparse, ConstraintException guard, epilog |
| AC-9 | All 4 subcommands in `bin/orchestrator` unchanged (NFR-6) | PASS | Same 4 `_*_main` functions; `_compute_cost_usd` wired at both call sites |

---

## NFR Assessment

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| NFR-1 | No `config/pricing.yaml` loaded at runtime after migration | PASS | No `_load_pricing` call sites remain in runtime path; stub raises `NotImplementedError` |
| NFR-2 | Migration idempotence | PASS | Confirmed by AC-2 |
| NFR-3 | All DB writes parameterized (no f-string SQL) | PASS | Spot-checked `upsert.py`, `record.py`, `cost_report.py`, `ingest-pricing.py` |
| NFR-4 | ≥ 90% coverage on modified modules | CONCERN | File-level: record.py 62%, upsert.py 63%, cost_report.py 69%. Pricing-specific paths are well-covered; misses are pre-existing CLI/renderer code. Not the primary rejection reason. |
| NFR-5 | Multi-level metrics invariant: `step_events` schema unchanged | PASS | No DDL changes to `step_events` |
| NFR-6 | No new CLI subcommands in `bin/orchestrator` | PASS | Binary diff confirms same 4 subcommands |

---

## Critical Findings

### C-1 [CRITICAL] `test_estimate_cost_sh.py:160` — Wrong env var kills AC-5

**File:** `config/scripts/orchestrator_next/tests/test_estimate_cost_sh.py`, line 160

**What's wrong:**
```python
# Current (broken):
env["ORCHESTRATOR_DB"] = db_path

# estimate-cost.sh line 149 reads:
local db_path="${METRICS_DB:-${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}/metrics.duckdb}"
```

T-15 correctly changed `estimate-cost.sh` to use `METRICS_DB` (matching `record.py`), but the test's `_base_env()` helper still injects `ORCHESTRATOR_DB`. The seeded DuckDB fixture is never found by the script. The script falls back to its YAML-based pricing path, producing different values.

**Impact:** `test_rewrite_output_matches_baseline_shape` fails with:
```
AssertionError: developer input pricing: expected 3.00, got 15.0
```
AC-5 is unverified. The acceptance criterion ("byte-equivalent output against seeded DuckDB fixture") is not proven by any passing test.

**Fix:** Change line 160:
```python
env["METRICS_DB"] = db_path
```

---

## Must-Fix Findings

### M-1 [MUST FIX] `record.py:54-55` — Dead stub contradicts T-6 spec

**File:** `config/scripts/orchestrator_next/record.py`, lines 52-55

**What's wrong:**
```python
# _load_pricing removed in T-6; pricing now comes from the DuckDB pricing table.
# cost_report.py has a stale import of _load_pricing — T-8 will clean that up.
def _load_pricing() -> dict:
    raise NotImplementedError("removed in T-6; use _lookup_price with a db connection")
```

T-6 spec: "delete `_load_pricing` lru_cache loader." The function was not deleted — it was converted to a `NotImplementedError` stub with a comment referencing T-8 cleanup that never happened (T-8 cleaned `cost_report.py`'s import, not this stub). The comment "T-8 will clean that up" is stale — T-8 is complete and this stub remains.

**Impact:** Any code that calls `_load_pricing()` will get a runtime `NotImplementedError` rather than a clear `AttributeError` / `ImportError`. The stub masks the deletion intent, makes the codebase misleading, and violates the T-6 spec.

**Fix:** Delete lines 52-55 entirely. Nothing imports `_load_pricing` after T-8 completed.

---

## Flags (Non-Blocking)

### F-1 [FLAG] `record.py` — `datetime.utcnow()` deprecated

`record.py` uses `datetime.datetime.utcnow()` which is deprecated in Python 3.12+ and raises `DeprecationWarning` in Python 3.14. Should use `datetime.datetime.now(datetime.timezone.utc)`. Not blocking for phase 1, but should be tracked.

### F-2 [FLAG] NFR-4 coverage gap

File-level coverage for modified modules is 62-69% vs 90% threshold. The gap is dominated by pre-existing CLI mains, renderer code, and upsert fan-out that were not changed by this feature. The pricing-specific code paths have good test coverage. Consider scoping NFR-4 to the diff, not the entire file, or accepting the current state with a documented carve-out.

### F-3 [FLAG] T-15 performance baseline unverifiable

T-15 commit message claims `_lookup_price` latency is "≤ 2× YAML baseline" but no benchmark number appears in any test or commit artifact. The claim cannot be independently verified.

### F-4 [FLAG] Seed SQL provenance comment

`0001_seed_pricing.sql` has a provenance comment referencing `config/pricing.yaml`. This is intentional per the developer's design (historical accuracy) and does not violate NFR-5 (the DDL/INSERT rows contain no `phase|feature|driver` columns). Documented here for traceability.

---

## Dimension Scores

| Dimension | Score | Key Issues |
|-----------|-------|------------|
| Spec Compliance | 7/10 | AC-5 unverified (C-1); T-6 stub not deleted (M-1) |
| Algorithm Correctness | 9/10 | Load-all cache correct; fallback chain correct; idempotent migrations |
| Security | 10/10 | All SQL parameterized; no hardcoded secrets |
| Performance | 9/10 | Cache accepted; T-15 baseline unverifiable (F-3) |
| Readability | 8/10 | Misleading stale stub comment (M-1) |
| Simplicity | 9/10 | Minimal implementation; no over-engineering |
| Code Quality (DRY) | 9/10 | No duplication; tested path == runtime path |
| Functional Completeness | 7/10 | AC-5 not functionally proven |
| Test Quality | 7/10 | Key test broken by wrong env var |
| **Overall** | **7.5/10** | |

---

## Issues to Fix Before Re-Review

1. **[CRITICAL] Fix env var mismatch in test:** `test_estimate_cost_sh.py:160` — change `ORCHESTRATOR_DB` to `METRICS_DB`
2. **[MUST FIX] Delete dead stub:** `record.py:52-55` — remove `_load_pricing()` function entirely

Both fixes are one-liners. Once applied and tests re-run confirming `test_rewrite_output_matches_baseline_shape` passes, the phase should clear 9.0.

---

## Re-review 2026-04-21

**Reviewer:** Reviewer Agent (independent verification)
**Commit reviewed:** `9e4b32c` (fix(pricing): implement-phase review findings)
**Branch:** `feature/pricing-table-in-duckdb`

### Resolved Findings

#### C-1 — RESOLVED

Verification: `rg 'ORCHESTRATOR_DB' .../tests/test_estimate_cost_sh.py` → exit 1, zero matches.

The fix applied all four occurrences in the file:

- Line 14 (docstring scenario c): `METRICS_DB=/nonexistent/path`
- Line 27 (design notes): `METRICS_DB is set to a tmp DuckDB file`
- Line 152 (`_base_env` docstring): `METRICS_DB → db_path if given`
- Line 160 (active env-var setter): `env["METRICS_DB"] = db_path`

Comments and docstrings updated consistently. No `ORCHESTRATOR_DB` string remains anywhere in the file.

Test confirmation: `pytest test_estimate_cost_sh.py::test_rewrite_output_matches_baseline_shape -q` → **1 passed in 0.40s**. AC-5 is now proven by a passing test.

#### M-1 — RESOLVED

Verification: `rg '_load_pricing\b' record.py` → exit 1, zero matches.

The stub function (4 lines) was deleted. Broader sweep across `config/scripts/orchestrator_next/`, `bin/`, and `scripts/` confirms the only remaining `_load_pricing` occurrences are:

- `cost_report.py`: `_load_pricing_for_model` — the new DuckDB-backed function (AC-6, intentional)
- Test files: `_load_pricing_for_model` calls and one comment "\_load_pricing is gone" (correct historical note)

No bare `_load_pricing` stub or import site anywhere in the runtime path.

### Test Suite Results

| Suite | Passed | Failed | Notes |
|-------|--------|--------|-------|
| `orchestrator_next/tests/` | 186 | 2 | 2 failures in `test_archive_backlog_cleanup.py` — pre-existing on `main`, unrelated to feature |
| `config/scripts/tests/` | 74 | 17 | Identical failure set on `main` — pre-existing, unrelated to feature |

The previously failing `test_rewrite_output_matches_baseline_shape` now passes. Zero new failures introduced by commit `9e4b32c`.

### Non-Blocking Items (carried forward)

- **F-1** `datetime.utcnow()` deprecation: still present in `record.py:191`. DeprecationWarning visible in test output. Acceptable carryover per re-review brief.
- **F-2** NFR-4 coverage gap: file-level coverage remains 62-69%; new code paths are well-covered; pre-existing code dominates the gap. Acceptable per re-review brief.
- **F-3** T-15 latency claim unverifiable: no benchmark artifact. Acceptable per re-review brief.

### Updated Dimension Scores

| Dimension | Prior | Re-review | Delta | Notes |
|-----------|-------|-----------|-------|-------|
| Spec Compliance | 7/10 | 10/10 | +3 | AC-5 now proven; all 9 ACs pass |
| Algorithm Correctness | 9/10 | 9/10 | — | Unchanged |
| Security | 10/10 | 10/10 | — | Unchanged |
| Performance | 9/10 | 9/10 | — | Unchanged |
| Readability | 8/10 | 10/10 | +2 | Dead stub removed; no misleading comments |
| Simplicity | 9/10 | 9/10 | — | Unchanged |
| Code Quality (DRY) | 9/10 | 9/10 | — | Unchanged |
| Functional Completeness | 7/10 | 10/10 | +3 | AC-5 functionally verified |
| Test Quality | 7/10 | 9/10 | +2 | Key test passes; pre-existing suite failures unrelated |
| **Overall** | **7.5/10** | **9.4/10** | **+1.9** | |

### Verdict: APPROVED

Both blocking findings (C-1, M-1) are resolved. All 9 acceptance criteria pass. Zero new test failures. The non-blocking flags (F-1, F-2, F-3) are accepted per re-review brief and do not affect the verdict.
