# Phase Review (Retry): implement — single-source-metrics-via-step-events

**Reviewer**: reviewer-agent  
**Date**: 2026-04-20  
**Re-review of**: commit `1f008dc` (T-21 fix)  
**Verdict**: approved  
**Overall score**: 9/10

---

## FINDING-1 Closure Evidence

**Integration test run (re-run independently):**

```
bash config/tests/test-metrics-pipeline-integration.sh
Results: 54 passed, 0 failed
```

The 4 previously-failing assertions that were SCOPE-MISMATCH FAILURES in the original review now all PASS:

```
PASS: resolution.pass_at_1 [R for feature — scope-mismatch if null]
PASS: resolution.pass_at_2 [R for feature — scope-mismatch if null]
PASS: resolution.regressions [R for feature — scope-mismatch if null]
PASS: resolution.regression_rate [R for feature — scope-mismatch if null]
```

FINDING-1 is **closed**.

---

## AC-1 Re-Verification

AC-1: "single `orchestrator metrics` call returns every field metrics-schema.md requires for schema=feature"

The integration test seeds a real DuckDB via `ingest-feature-metrics.py` (the real ingest path, not direct `feature_metrics` seeding), then calls `orchestrator metrics --change-id integration-test-abc --format json` and asserts all R fields. All 54 assertions pass, including the 4 resolution fields that were NULL before the fix.

**AC-1 status: PASS** (previously PARTIAL)

---

## Regression Check

Pytest suite:

```
pytest config/scripts/orchestrator_next/tests/ -q
2 failed, 144 passed in 0.97s
```

The 2 failures are the pre-existing `test_archive_backlog_cleanup.py` failures established at baseline capture (step `capture-test-baseline`). No new failures introduced by commit `1f008dc`.

**Regression check: PASS**

---

## T-21 Diff Review (commit 1f008dc)

### What was added

A new `compute_resolution()` function (lines 118–164) derives `pass_at_1`, `pass_at_2`, `regressions`, and `regression_rate` from `state.yaml` data. It is wired into `main()` and spread into the `upsert_feature_metrics()` call via `**resolution`.

### Correctness assessment

- **Division-by-zero guard**: `if not tasks_total` on line 140 catches both `None` and `0` correctly; returns all-None (spike path safe).
- **Negative `pass_at_1` guard**: `max(0, tasks_total - retries_total)` on line 150 prevents negative values when retries exceed tasks.
- **`pass_at_2` denominator**: `tasks_total` (not `tasks_completed`), which is correct — measures completion rate.
- **`regression_rate` denominator**: `tasks_total`, consistent with the spec's description of regression_rate.
- **`tasks_completed` type guard**: line 148 coerces non-int to 0, defensive and appropriate.
- **All 4 fields wired to upsert**: confirmed in the diff (`**resolution` passed to `upsert_feature_metrics()`).

### No new critical or important findings

---

## New Findings from T-21 Diff

### FINDING-NEW-1 [SUGGESTION] `quarantine_events` parameter is unused in function body

`scripts/inline/ingest-feature-metrics.py:123` — `compute_resolution()` accepts `quarantine_events` and `main()` passes it (line 389), but the function body never references the variable. The docstring acknowledges the omission with a rationale. The dead parameter adds cognitive overhead for future readers. Should either be used (reduce `tc` for quarantined tasks) or removed from the signature and call site.

This is not a correctness issue today since the docstring explains why the formula stays consistent without it. **Not blocking.**

### FINDING-NEW-2 [SUGGESTION] Unit test `test-ingest-feature-metrics.sh` not extended with resolution field assertions

The prior review's T-21 task description specified two files to modify: `ingest-feature-metrics.py` and `config/scripts/__tests__/test-ingest-feature-metrics.sh`. Only the Python file was changed. Integration test coverage (`test-metrics-pipeline-integration.sh`) fully covers the 4 fields for AC-1, so this is not a coverage gap in practice. However, the unit test remains silent on the new fields. **Not blocking.**

### FINDING-NEW-3 [SUGGESTION] `pass_at_1` approximation uses all-step retries, not task-only retries

`compute_retries()` sums all keys in `state.yaml`'s `retries` section, which can include non-task step retries (e.g., `capture-test-baseline`, `run-phase-review`). This means `retries_total` fed to `pass_at_1` may count non-task retries, causing `pass_at_1` to be lower than the true per-task pass rate. The docstring documents the approximation and its limitations. Matches the spec's intent ("tightest approximation possible without per-task retry records"). **Not blocking — by design and documented.**

---

## Dimension Scores (Re-scored)

| Dimension | Score | Reasoning |
|-----------|-------|-----------|
| spec_compliance | 9/10 | AC-1 now fully passes: all R fields for feature schema return non-null values from the real ingest pipeline. All 6 ACs verified. |
| correctness | 9/10 | `compute_resolution()` correctly guards against division-by-zero and negative values. All 4 fields derived and wired. Logic matches spec description. |
| security | 9/10 | Unchanged from prior review — slug guard at all DuckDB entry points, all SQL parameterised, no secrets. |
| simplicity | 9/10 | Unchanged — wrappers remain under budget, no premature abstractions, no dead code paths. |
| code_quality | 9/10 | Clean addition with docstring explaining the approximation rationale. One unused parameter (FINDING-NEW-1) is a minor quality smell, not a critical issue. |

**Overall (min of dimensions): 9/10**

No +1 bonus: the `quarantine_events` dead parameter (FINDING-NEW-1) and the unit test gap (FINDING-NEW-2) fall short of "every artifact exceeds minimums." 9 is the honest score.

---

## Prior Retro Items (Unchanged)

**FINDING-RETRO-1 (ISSUE-33)**: `dispatch.py._find_completed_step` does not honor `repeat_until` when completed entries exist. This is an orchestrator infrastructure bug, not introduced by this feature. Status unchanged — requires a separate fix task.

**FINDING-RETRO-2**: `register-repo.test.sh` T-5b — 2 assertions test pre-FR-11 buggy behavior. These are not regressions from this feature. A post-merge task should update the assertions to reflect the correct invariant. Status unchanged.

Prior review's retro items (ISSUE-33, register-repo.test.sh T-5b) unchanged.

---

## Verdict

**approved** — Score: 9/10

FINDING-1 is verifiably closed: `compute_resolution()` correctly computes all 4 required fields, they are wired into the upsert, and the integration test confirms 54/54 passing (down from 50/54). No regressions: pytest still shows 144 passing, 2 pre-existing failures. Three new suggestion-tier findings noted (unused parameter, missing unit-test extension, documented approximation caveat) — none blocking. The core architecture remains sound.
