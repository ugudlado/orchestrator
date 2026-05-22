# Tasks — Unify Cost Computation Between Python and Shell

## Group 1 — Extract the shared pricing module

### [x] T-1: Verify existing pricing tests as the extraction regression guard (no RED — mechanical change)
**Why:** AC-1, AC-2 — the module extraction must not change behavior; the existing pricing tests are the guard.  
**Files:** config/scripts/orchestrator_next/tests/test_pricing_lookup.py, config/scripts/orchestrator_next/tests/test_pricing_lookup_dated.py, config/scripts/orchestrator_next/tests/test_record_cost_compute.py  
**Change:** No new test code. Run the three existing pricing test files on HEAD and confirm they are green before extraction; record the baseline. These tests already import `_lookup_price`/`_pricing_cache`/`_load_routes` from `orchestrator_next.record` and are the regression guard for T-2.  
**Test scenarios:**
- test_pricing_lookup.py — all scenarios green on HEAD
- test_pricing_lookup_dated.py — all 4 scenarios green on HEAD
- test_record_cost_compute.py — all 7 cases green on HEAD

### [x] T-2: Extract pricing logic into orchestrator_next/pricing.py and re-export from record.py (GREEN)
**Why:** AC-1, AC-2, Decision D-8 — one implementation of routes resolution + DuckDB pricing; record.py keeps a stable public symbol surface.  
**Files:** config/scripts/orchestrator_next/pricing.py (new), config/scripts/orchestrator_next/record.py  
**Change:** Create pricing.py and move `_orchestrator_home`, `_load_routes`, `_LOOKUP_SQL`, `_LOAD_ALL_SQL`, `_pricing_cache`, `_ensure_pricing_cache`, `_DATED_MODEL_SUFFIX_RE`, `_lookup_price`, `_billable_token_units`, `_compute_cost_usd` verbatim from record.py:580-789 (logic byte-equivalent). In record.py, delete those bodies and add `from orchestrator_next.pricing import` of the same names so they are re-exported by reference. Before changing any `[record]` stderr prefix, grep tests for the literal prefix (D-8) — keep `[record]` if pinned.  
**Test scenarios:**
- all T-1 tests pass unchanged (re-export binds the same `_pricing_cache` and `_load_routes` objects)
- `from orchestrator_next.record import _lookup_price` still resolves
- `_record_mod._pricing_cache.clear()` clears the cache the production path reads
- type-check clean

**depends:** T-1

### [x] T-3: Review checkpoint — pricing module extraction (phase gate)
**Why:** phase gate — confirm extraction integrates cleanly before adding the CLI.  
**Test scenarios:**
- type-check clean
- test_pricing_lookup.py, test_pricing_lookup_dated.py, test_record_cost_compute.py all green
- no pricing functions duplicated between record.py and pricing.py

**depends:** T-2

## Group 2 — Pricing CLI entry point

### [x] T-4: Write tests for the pricing CLI bulk entry point (RED — tests must fail)
**Why:** AC-6, AC-7 — the CLI is new behavior; tests must pin the JSON-array contract and date/dated-suffix parity before implementation.  
**Files:** config/scripts/orchestrator_next/tests/test_pricing_cli.py (new)  
**Change:** Add subprocess-driven tests that invoke `python3 -m orchestrator_next.pricing --agents …` with `PYTHONPATH` set to config/scripts and a seeded tmp DuckDB (reuse the `ensure_schema` seeding pattern from test_estimate_cost_sh.py). Tests fail today because pricing.py has no `main`/`__main__`.  
**Test scenarios:**
- `--agents developer architect` emits a JSON array of 2 objects, each with `agent`, `backend`, `model`, `input_usd`, `output_usd`, `cache_read_usd`, `cache_creation_usd`
- all four pricing columns present and non-null for a seeded model (AC-7)
- exactly one Python process is spawned for an N-agent call (single invocation)
- `--agents` listing an agent NOT in routes.yaml still yields a priced JSON entry (`__default__` pricing) — archive-observed-agent parity (AC-7)
- invoking the CLI with no `--agents` flag exits non-zero with a usage error on stderr, empty stdout (AC-7, D-6 — pure pricer, no agent discovery)
- invoking the CLI with `--agents` and zero names exits non-zero with a usage error (AC-7, D-6)
- a future-dated pricing row is NOT applied before its `effective_from` (AC-6, D-3)
- a dated-suffix model id resolves to its base-model rate (AC-6, D-4)
- DB-absent (`METRICS_DB=/nonexistent`) → CLI exits non-zero, empty stdout, stderr diagnostic (AC-4, D-2)

**depends:** T-3

### [ ] T-5: Implement the pricing CLI bulk entry point in pricing.py (GREEN — make tests pass)
**Why:** AC-6, AC-7, Decisions D-2, D-5, D-6 — one bulk CLI call replaces the Bash pricing logic.  
**Files:** config/scripts/orchestrator_next/pricing.py  
**Change:** Add `main(argv)` and a `__main__` guard to pricing.py. Parse `--agents <name>…` — the flag is required and must carry a non-empty list; a missing flag or empty list exits non-zero with a usage error on stderr and no stdout (D-6 — the CLI is a pure pricer and does NOT discover agents). Resolve metrics DB via `$METRICS_DB` else `$ORCHESTRATOR_HOME/metrics.duckdb` (same convention as record.py:1844-1847). For each supplied agent, resolve agent→backend→model via the routes chain and call `_lookup_price`; an agent absent from routes.yaml resolves the same way `_compute_cost_usd` handles an unrouted agent (`__default__` pricing). Emit a JSON array with all four pricing columns (D-5). If the DB file is absent, write a stderr diagnostic and exit non-zero with no stdout (D-2).  
**Test scenarios:**
- all T-4 tests pass
- type-check clean

**depends:** T-4

### [ ] T-6: Review checkpoint — pricing CLI (phase gate)
**Why:** phase gate — confirm the CLI contract is stable before estimate-cost.sh depends on it.  
**Test scenarios:**
- type-check clean
- test_pricing_cli.py green
- full pricing test suite still green

**depends:** T-5

## Group 3 — Rewire estimate-cost.sh and reconcile divergences

### [ ] T-7: Update estimate-cost.sh tests for fail-loud DB-absent and CLI delegation (RED — scenario (c) rewritten)
**Why:** AC-3, AC-4, Decision D-2 — the locked decision removes the hardcoded fallback; scenario (c) must assert fail-loud, and parity scenarios must still hold against the CLI-backed script.  
**Files:** config/scripts/orchestrator_next/tests/test_estimate_cost_sh.py  
**Change:** Rewrite `test_db_absent_uses_default_rates_and_exits_zero` (scenario c) — rename to `test_db_absent_fails_loud_no_fabricated_rates` and assert that with `METRICS_DB=/nonexistent` the estimator does NOT emit fabricated `15.00 75.00 1.50` pricing: either estimate-cost.sh exits non-zero, or its `route_preview` carries no per-agent pricing block / surfaces an unavailable state. Update the module docstring's scenario (c) description. Scenarios (a), (b), (d) are unchanged. This test fails today (script still returns default rates and exits 0).  
**Test scenarios:**
- scenario (c) rewritten: DB-absent → no `15.00 75.00 1.50` rates in output; estimator surfaces failure (non-zero exit or unavailable state)
- scenarios (a), (b), (d) assertions unchanged and still pass after T-8

**depends:** T-6

### [ ] T-8: Rewire estimate-cost.sh to call the pricing CLI; add native_haiku to routes.yaml (GREEN)
**Why:** AC-3, AC-4, AC-5, AC-8, Decisions D-1, D-2, D-6 — replace the Bash reimplementation with one CLI call; preserve routes ∪ archive-observed agent-list union; routes.yaml becomes the native-backend source of truth.  
**Files:** scripts/estimate-cost.sh, scripts/routes.yaml  
**Change:** In estimate-cost.sh, delete the pricing-resolution Bash — `get_backend`, `get_model`, `resolve_native`, `lookup_pricing` and the `AGENT_BACKEND_MAP`/`BACKEND_MODEL_MAP` awk parsers (lines 59-170). Keep the agent-list assembly at lines 262-281 unchanged in semantics: estimate-cost.sh still builds `ALL_AGENTS_LIST` as the deduplicated union of routes.yaml agents and archive-observed agents (`PER_AGENT_SHARE`) — a small awk pass over the routes `agents:` block still supplies the routes side of the union. Call `PYTHONPATH="$ORCHESTRATOR_HOME/config/scripts" python3 -m orchestrator_next.pricing --agents $ALL_AGENTS_LIST` once with the full explicit list; parse the JSON array with the python3 the script already uses; read `input_usd`/`output_usd`/`cache_read_usd` into the existing `in_price`/`out_price`/`cache_price` vars. Propagate a non-zero CLI exit. Keep the script Bash 3.2-compatible — no `declare -A`/`mapfile`/`readarray`. In routes.yaml `backends:`, add `native_haiku: claude-haiku-4-5`.  
**Test scenarios:**
- all T-7 tests pass (scenarios a, b, c, d)
- `grep -c` for `get_backend`/`get_model`/`resolve_native`/`lookup_pricing` in estimate-cost.sh returns 0 (AC-3)
- an archive-observed agent NOT present in routes.yaml still appears in the `route_preview` agents list (AC-8 — routes ∪ archive union preserved)
- scenario (d) — script runs clean under bash 3.2, no `declare -A` errors
- routes.yaml resolves `native_haiku` → `claude-haiku-4-5` (AC-5)

**depends:** T-7

### [ ] T-9: Review checkpoint — full unification (phase gate)
**Why:** phase gate — confirm both paths share one implementation and all tests pass before completion.  
**Test scenarios:**
- type-check clean
- full test suite green (test_pricing_lookup, test_pricing_lookup_dated, test_record_cost_compute, test_pricing_cli, test_estimate_cost_sh)
- no duplicated pricing logic remains in estimate-cost.sh or record.py
- preview-route.sh still produces a route_preview block end-to-end

**depends:** T-8
