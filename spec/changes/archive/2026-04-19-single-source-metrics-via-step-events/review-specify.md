# Phase Review: specify — single-source-metrics-via-step-events (Iter 3)

**Reviewer:** Staff Engineer (independent)
**Date:** 2026-04-20
**Iteration:** 3 (iter 1 = initial backlog; iter 2 = aborted specify; iter 3 = this run)
**Threshold:** review_score.min = 9 | critical_cap = 5 | important_cap = 7 | green_base = 9
**Bonus eligibility:** First attempt (iter 3 is first attempt on the *correctly scoped* spec) — eligible if all criteria met.

---

## Verdict: approved_with_changes

**Overall score: 7/10**

| Dimension | Score | Notes |
|---|---|---|
| spec_compliance | 9 | All phase assertions met, OQs resolved, ACs testable |
| correctness | 7 | TDD pairing violated in T-11 (important finding) |
| security | 9 | NFR-1 parameterized SQL, slug guard documented |
| simplicity | 9 | Approach B is the minimal correct solution |
| code_quality | 9 | Clean architecture, consistent patterns |
| **Overall (min)** | **7** | Capped by `important_cap: 7` on correctness dimension |

Score bonus (+1) not awarded: correctness dimension below threshold.

---

## Phase Verify Assertions

| Assertion | Status | Evidence |
|---|---|---|
| `spec.md` exists in state dir | PASS | `/Users/spidey/code/orchestrator/.state/single-source-metrics-via-step-events/spec.md` |
| `design.md` exists in state dir | PASS | `/Users/spidey/code/orchestrator/.state/single-source-metrics-via-step-events/design.md` |
| `tasks.md` exists with at least one task | PASS | 20 tasks (T-1 through T-20) |
| `spec.md` has Acceptance Criteria section with testable criteria | PASS | AC-1 through AC-6, all reference concrete test commands or grep criteria |

---

## ISSUE-32 Closure Verification (Primary Check)

**Finding: ISSUE-32 is fully resolved. The hybrid JSONL read pattern is eliminated.**

The core finding in retro.md §ISSUE-32 was: iter-2 design had `compute-swe-metrics.sh` keeping JSONL as a second read source inside the wrapper — both DuckDB query AND JSONL parsing co-existing at read time, contradicting the single-source premise.

Evidence that the new design eliminates this:

1. **design.md §Goals (Non-Goals):** "compute-swe-metrics.sh and read-sub-state-metrics.sh contain zero parsing, zero `git log`, zero JSONL reads." — This is stated as a design goal, not just aspiration.

2. **design.md §Components #6 (`compute-swe-metrics.sh` rewrite):** The script body shells out exclusively to `orchestrator metrics --format json` and projects the result via `yq`. No JSONL path, no `git log` call, no `tasks.md` read.

3. **design.md §Components #7 (`read-sub-state-metrics.sh` rewrite):** Same pattern — `orchestrator metrics --format json` | `yq`. No secondary data source.

4. **design.md §Components #4 (`ingest-feature-metrics`):** This step DOES read `tasks.md + git log + state.yaml`, but this is correct — it is the *ingest* step that runs once at complete-phase time to populate DuckDB. It is not a read-time wrapper. This is approach B as intended.

5. **design.md §Data Flow:** Step 4 shows `compute-swe-metrics` doing ONLY `orchestrator metrics --change-id X` with DuckDB join. No dual-path.

6. **spec.md §Alternatives Considered:** Approach A (rejected) is explicitly documented as the prior approach that kept JSONL reading, with the ISSUE-32 retro cited by reference.

**Conclusion:** The iter-2 hybrid pattern (JSONL read inside wrapper at read time) is definitively eliminated in this design. DuckDB is the sole runtime source for both wrapper scripts.

---

## Open Questions Verification (All 7 from discovery.md)

| OQ | Resolution | spec.md Section | Status |
|---|---|---|---|
| OQ-1: `orchestrator metrics` vs `orchestrator cost` subcommand relationship | Separate subcommand; `cost` stays narrow, `metrics` is broad | §Decisions OQ-1 | RESOLVED |
| OQ-2: `ingest-feature-metrics` failure policy | Fail loud — non-zero exit blocks archive | §Decisions OQ-2 | RESOLVED |
| OQ-3: Complete-phase ordering enforcement | Both `_complete-phase.yaml` and `test-complete-phase-order.sh` updated in same task (T-11) | §Decisions OQ-3 | RESOLVED |
| OQ-4: Spike complete-phase | `ingest-feature-metrics` runs for feature + bugfix only; spike phase file untouched | §Decisions OQ-4 | RESOLVED |
| OQ-5: `read-sub-state-metrics.sh` output contract | Stays narrow (3 fields only); verified against `autopilot-session-rollup.sh` | §Decisions OQ-5 | RESOLVED |
| OQ-6: WORKFLOW_STATE_DIR / ORCHESTRATOR_WORKFLOW_DIR ambiguity | Convention: all workflow artifacts in `$REPO_ROOT/.state/<slug>/` | §Decisions OQ-6 | RESOLVED |
| OQ-7: `_totals()` pricing join | `pricing.yaml` at query time via `_load_pricing()` pattern (not DuckDB table) | §Decisions OQ-7 | RESOLVED |

All 7 OQs have concrete decisions with rationale. None deferred.

---

## Acceptance Criteria Review

| AC | Testable? | Verification Mechanism | Status |
|---|---|---|---|
| AC-1: `orchestrator metrics` returns all schema-required fields | Yes | `bash config/tests/test-orchestrator-metrics-json-shape.sh` | PASS |
| AC-2: `compute-swe-metrics.sh` byte-compat before/after rewrite | Yes | `bash config/scripts/__tests__/compute-swe-metrics-projection.test.sh` with golden fixture | PASS |
| AC-3: Missing tasks.md causes non-zero exit + blocks archive | Yes | `bash config/scripts/__tests__/test-ingest-feature-metrics.sh` UC-E1 path | PASS |
| AC-4: Ordering test asserts `mark < ingest < compute` | Yes | `bash config/tests/test-complete-phase-order.sh` | PASS |
| AC-5: register-repo.sh invariant rejects NULL-token rows | Yes | `bash config/tests/test-register-repo-usage-invariant.sh` | PASS |
| AC-6: `orchestrator cost` includes cache/turns in totals after archive | Yes | `orchestrator cost --change-id X --format json` + grep for keys | PASS |

All ACs are testable with literal commands or verifiable outputs.

---

## TDD Pairing Verification

Scanning all 20 tasks for RED-before-GREEN pairs:

| Pair | RED | GREEN | Properly Split? |
|---|---|---|---|
| turns column | T-1 | T-2 | YES |
| `_totals()` widening | T-3 | T-4 | YES |
| `feature_metrics` DDL | T-5 | T-6 | YES |
| `orchestrator metrics` subcommand | T-7 | T-8 | YES |
| `ingest-feature-metrics` step | T-9 | T-10 | YES |
| Complete-phase wiring + ordering test | T-11 (bundles both) | — | **NO — VIOLATION** |
| `compute-swe-metrics.sh` rewrite | T-12 | T-13 | YES |
| `read-sub-state-metrics.sh` rewrite | T-14 | T-15 | YES |
| register-repo invariant | T-16 | T-17 | YES |
| T-18 (fix 5 broken paths) | No RED test | T-18 | Acceptable — mechanical path fix, no new logic |
| T-19 (integration test) | No implementation pair | T-19 | Acceptable — E2E tests have no impl pair by convention |
| T-20 (phase gate) | — | — | Gate task, not an impl — OK |

---

## Findings

### Important (triggers `important_cap: 7`)

**FINDING-1: T-11 bundles RED test + GREEN implementation in a single task (TDD pairing violation)**

- **Severity:** Important
- **File:section:** `tasks.md:T-11`
- **What's wrong:** T-11 describes both wiring `ingest-feature-metrics` into `_complete-phase.yaml` (GREEN: implementation) and extending `test-complete-phase-order.sh` with ordering assertions (RED: test). Both are in a single task with a single `Verify` block. The `tdd_required: true` flag requires a preceding failing test task before each implementation task.
- **Why it matters:** The iter-2 reviewer explicitly flagged a structurally identical T-7/T-8 issue ("broken TDD pair"). The current artifacts fix the T-7/T-8 pair but reproduce the same pattern at T-11. Consistent TDD discipline means the test must fail first (before the YAML is modified) — bundling them means the developer could write both in any order.
- **Specific fix:** Split T-11 into:
  - **T-11a (RED):** Extend `test-complete-phase-order.sh` with `POS_INGEST` assertions (test fails because `ingest-feature-metrics` is not yet in `_complete-phase.yaml`). Verify: `bash config/tests/test-complete-phase-order.sh` FAILS (red).
  - **T-11b (GREEN):** Insert `ingest-feature-metrics` into `_complete-phase.yaml` between `mark-change-completed` and `compute-swe-metrics`. Verify: `bash config/tests/test-complete-phase-order.sh` PASSES; `_complete-phase-spike.yaml` unchanged.
  - Update `depends:` chain accordingly (T-11a depends T-10; T-11b depends T-11a).

### Green (no score impact)

**FINDING-2: T-18 has no preceding RED test (acceptable — mechanical path fix)**

- **Severity:** Green
- **File:section:** `tasks.md:T-18`
- **Note:** Fixing string literals in 5 test files (`config/scripts/compute-swe-metrics.sh` → `scripts/inline/compute-swe-metrics.sh`) is a mechanical path correction with no new logic. TDD pairing is not required for mechanical fixes. The tests themselves become the red-to-green when the path is corrected. This is not a finding.

**FINDING-3: `ingest-feature-metrics` step contract uses `WORKFLOW_STATE_DIR` in instruction (minor)**

- **Severity:** Green
- **File:section:** `design.md §Components #4`, step contract `instruction:` block
- **Note:** The step contract instruction says `--state-yaml "$WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml"`. Per OQ-6 resolution, `WORKFLOW_STATE_DIR` should resolve to `$REPO_ROOT/.state/` (the main repo). This is documented and the OQ-6 resolution in spec.md §Decisions is clear. No fix required, but implementer should ensure the env var is set correctly at spawn time per the OQ-6 convention.

---

## cost.model Consistency Check (Iter-2 Finding Recurrence)

The iter-2 reviewer found conflicting `cost.model` data-source definitions across multiple sections. Checking for recurrence in iter-3 artifacts:

- **design.md §Components #2 (`_totals()` widening):** Dominant model resolved via `SELECT model ... GROUP BY model ORDER BY SUM(input_tokens) DESC LIMIT 1` from `step_events`. Returned as `model` key in totals dict.
- **design.md §Components #5 (`orchestrator metrics` JSON shape):** `cost.model` shown as `"claude-sonnet-4-5"` — consistent with dominant-model from step_events.
- **design.md §Error Handling:** Missing pricing entry falls back to `pricing.default` — consistent with `record.py` pattern.
- **spec.md FR-2:** `model` listed as one of the new keys in `totals` block.

All three sections are consistent: `step_events.model` → dominant query → `pricing.yaml` lookup → `cost.model` in output. No contradiction. Prior iter-2 finding does NOT recur.

---

## Fix Tasks (generated for approved_with_changes path)

### FIX-1: Split T-11 into T-11a (RED) and T-11b (GREEN)

**Scope:** `tasks.md` only — split one task into two, update `depends:` chain.

**Approach:** Remove T-11 as written. Insert:
- T-11a: extend ordering test with failing assertions (no YAML change yet)
- T-11b: wire YAML + verify test passes

**Verify:** `tasks.md` has 21 tasks (T-1 through T-20, with T-11 split to T-11a + T-11b); each implementation step has a preceding RED test step; `tdd_required` rule is satisfied for all 10 implementation pairs.

---

## Summary

The iter-3 artifacts successfully eliminate the hybrid JSONL read pattern that caused iter-2 to abort (ISSUE-32). Approach B is correctly specified: DuckDB widens at ingest time; wrappers are pure projections; `orchestrator metrics` is the single SQL query plane. All 7 open questions are resolved with concrete decisions and rationale. All 6 acceptance criteria are testable with literal commands.

One important finding blocks a clean `approved` verdict: T-11 bundles a RED test and GREEN implementation in a single task, violating `tdd_required: true`. This is structurally identical to the T-7/T-8 issue flagged by the iter-2 reviewer. The fix is mechanical (split into T-11a / T-11b) and does not require any design changes.

After FIX-1 is applied to `tasks.md`, the artifacts are ready for the implement phase.

<\!-- reviewed by: staff-engineer reviewer agent -->
<\!-- iter: 3 -->
<\!-- date: 2026-04-20 -->
