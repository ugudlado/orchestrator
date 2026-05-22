# Phase Review: ORC-71 — Unify Cost Computation Between Python and Shell

Phase: implement (run-phase-review)
Reviewer: independent verification (Mode 2)
Date: 2026-05-22
Branch: feature/orc-71 @ b212cee

| Dimension | Score | Key Issues |
|-----------|-------|------------|
| Spec Compliance | 9/10 | All 8 ACs verified with evidence; full UC traceability |
| Correctness | 9/10 | Value + shape parity proven against pre-refactor baseline |
| Security | 9/10 | Read-only DB connect; no injection surface; no secrets |
| Simplicity | 9/10 | Verbatim extraction; thin re-export bridge; no over-engineering |
| Code Quality | 9/10 | Clean; one net-new deprecation warning (non-blocking) |
| **Overall** | **9/10** | **PASS** |

## Verification

- **Type-check:** No mypy/pyright/ruff configured in the project; project.yaml
  defines only a `test` command. `python3 -m py_compile pricing.py record.py`
  → COMPILE OK. Import resolution clean.
- **Tests (full suite):** `pytest config/scripts/orchestrator_next/tests/ -q`
  → **493 passed, 1 failed, 28 warnings**.
  - The 1 failure — `test_prose_contracts.py::test_feature_schema_required_inputs_have_a_producer`
    (`design-and-draft-artifacts: required input 'diagnosis_result' has no
    producer`) — was independently re-run on baseline commit 28dd8a0 via a
    fresh worktree and **fails identically there**. Pre-existing, unrelated to
    ORC-71 (a feature-schema producer gap). Not attributable to this phase.
- **Pricing/estimate subset:** 37 passed (test_pricing_lookup,
  test_pricing_lookup_dated, test_record_cost_compute, test_pricing_cli,
  test_estimate_cost_sh).
- **Build:** N/A (Python package, no build step).
- **Uncommitted changes:** only the untracked artifacts design.md / discovery.md
  under spec/changes/orc-71/ (workflow artifacts, expected). 6 feature commits
  on branch (T-2/T-4/T-5/T-7/T-8/T-9); T-1/T-3/T-6/T-9 are verify-only.

## Task Completeness

tasks.md: 9 `### [x] T-N:` headers, 0 `### [ ]` unchecked. No quarantine
events in state.yaml. Phase is complete — no `incomplete_phase`.

## Acceptance Criteria — verified with evidence

| AC | Status | Evidence |
|----|--------|----------|
| AC-1 | PASS | `_compute_cost_usd` (now imported from `orchestrator_next.pricing`) returns identical `cost_usd` to pre-refactor record.py. Runtime parity check against a seeded DuckDB: architect=53.7375, developer=10.7475, reviewer=10.7475, ideator=53.7375, unrouted=53.7375 — byte-identical to baseline 28dd8a0 record.py for the same inputs. All 7 `test_record_cost_compute.py` tests pass unchanged. |
| AC-2 | PASS | Re-export-by-reference verified by object identity: all 8 symbols (`_pricing_cache`, `_load_routes`, `_compute_cost_usd`, `_lookup_price`, `_DATED_MODEL_SUFFIX_RE`, `_ensure_pricing_cache`, `_billable_token_units`, `_orchestrator_home`) satisfy `record.X IS pricing.X` → True. record.py uses a plain `from orchestrator_next.pricing import (...)` — no `try/except ImportError`. test_pricing_lookup.py + test_pricing_lookup_dated.py pass unchanged. |
| AC-3 | PASS | `grep -cE 'get_backend\|get_model\|resolve_native\|lookup_pricing' config/scripts/estimate-cost.sh` → **0**. Script calls `python3 -m orchestrator_next.pricing` once per preview. test_estimate_cost_sh.py scenarios (a),(b),(d) pass. |
| AC-4 | PASS | `METRICS_DB=/nonexistent` → CLI exits 1, empty stdout, stderr `[pricing] metrics DB not found ...`. estimate-cost.sh propagates non-zero. No fabricated rates: `15.00 75.00 1.50` literal appears only in `register-repo.sh` INSERT rows and `0001_seed_pricing.sql` seed data (legitimate DB rows), never as a script fallback. Scenario (c) `test_db_absent_fails_loud_no_fabricated_rates` passes. |
| AC-5 | PASS | `scripts/routes.yaml` `backends:` contains `native_haiku: claude-haiku-4-5` (line 30). Resolution is via routes.yaml, not a hardcoded map. |
| AC-6 | PASS | `effective_from <= now` filter and `-YYYYMMDD` suffix strip live in shared `_lookup_price`/`_DATED_MODEL_SUFFIX_RE`; both the in-process path and CLI use the same function. test_pricing_cli.py dated/future-dated scenarios pass (2 passed). |
| AC-7 | PASS | CLI with `--agents architect developer some-archive-only-agent` → JSON array of 3 objects, each with all 4 pricing columns (`input_usd, output_usd, cache_read_usd, cache_creation_usd`) non-null; archive-observed unrouted agent priced via `__default__` (15/75/1.5/18.75). No `--agents` → exit 2, stderr usage, empty stdout. Empty `--agents` → exit 2. One Python process per N-agent call. |
| AC-8 | PASS | End-to-end: archive fixture with `per_agent_tokens` naming `ghost-agent` (absent from routes.yaml). `estimate-cost.sh` route_preview includes `ghost-agent` — `{backend: unrouted, model: unknown, pricing: {15/75/1.5}, share: 0.375, cost_estimate_usd: 0.0945}`. Routes ∪ archive-observed union preserved. |

**8/8 ACs PASS. No AC failures → no critical finding in spec_compliance.**

## Parity Verification (learned rule — byte-compatible replacement)

This is a verbatim-extraction + producer-replacement change, so parity was
checked at both value and shape level, not key-presence alone:

- **pricing.py core logic vs baseline record.py:580-789** — diff of the
  non-comment, non-blank lines shows the *only* delta is the new module
  docstring and the relocated import block. Pricing functions are byte-identical.
- **Value parity** — `_compute_cost_usd` produces identical cost floats for
  architect/developer/reviewer/ideator/unrouted on baseline vs refactored.
- **route_preview shape parity** — live `estimate-cost.sh` run vs the captured
  baseline fixture `estimate_cost_before.txt`: identical top-level keys
  (`agents, estimate, estimate_reason, schema`; `generated_at` is a stripped
  timestamp), identical agent-object keys (`agent, backend, cost_estimate_usd,
  model, pricing, share, tokens_estimate`), identical 8-agent set. **No
  top-level output key reduction.**

## Use Case Traceability

All 6 discovery use cases (UC-1, UC-2, UC-3, UC-E1, UC-E2, UC-E3) are traced
by at least one AC. design.md structurally complies with
contracts/artifact-formats.md § Design Format Contract — all required sections
present, 3 approaches with pros/cons, Selected Approach references the
simplicity gate. tasks.md uses the `### [x] T-N:` header form.

## Baseline Comparison (non-blocking)

Average `review_score_avg` across 8 archived feature-schema runs: **6.72**.
Current overall **9** is 2.28 *above* baseline — no regression warning.

## Critical Issues

None.

## Important Issues

None.

## Minor Issues (non-blocking — SUGGESTIONS only, no tasks generated)

1. **[SUGGESTION] `config/scripts/orchestrator_next/pricing.py:319`** — the CLI
   `main()` introduces a *net-new* `datetime.utcnow()` call, which emits a
   `DeprecationWarning` on Python 3.12+. The `:221` occurrence is an acceptable
   verbatim lift from baseline record.py (behavior-preserving, D-8). The `:319`
   occurrence is new code and could have used
   `datetime.now(timezone.utc).replace(tzinfo=None)` to keep naive-UTC parity
   with the legacy `_lookup_price` path without adding a fresh deprecation.
   Cosmetic; does not affect correctness.
2. **[SUGGESTION] `spec/changes/orc-71/tasks.md` T-8 `Files:` line** — lists
   `scripts/estimate-cost.sh`; the real path is `config/scripts/estimate-cost.sh`.
   The developer edited the correct file (AC-3 grep + tests confirm). Doc typo
   only.

## Scoring Rationale

All 5 dimensions all-green → 9 each. overall = MIN(9,9,9,9,9) = 9.
No +1 bonus: the two minor artifact-quality nits above (a net-new deprecation
warning in new code, and a tasks.md path typo) mean "every artifact exceeds
minimums" is not met. Bonus correctly withheld.

## Verdict: PASS (overall 9, no critical findings)
