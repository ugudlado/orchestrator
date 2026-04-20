# Phase Review: implement — single-source-metrics-via-step-events

**Reviewer**: reviewer-agent  
**Date**: 2026-04-20  
**Verdict**: approved_with_changes  
**Overall score**: 7/10

---

## Verification Gates

| Check | Command | Result |
|-------|---------|--------|
| Pytest | `pytest config/scripts/orchestrator_next/tests/ -q` | 144 passed, 2 pre-existing failures in `test_archive_backlog_cleanup.py` — PASS |
| Bash test: orchestrator metrics JSON shape | `bash config/tests/test-orchestrator-metrics-json-shape.sh` | 50/50 PASS |
| Bash test: complete-phase order | `bash config/tests/test-complete-phase-order.sh` | 13/13 PASS |
| Bash test: register-repo invariant | `bash config/tests/test-register-repo-usage-invariant.sh` | 4/4 PASS |
| Bash test: ingest-feature-metrics | `bash config/scripts/__tests__/test-ingest-feature-metrics.sh` | 12/12 PASS |
| Bash test: compute-swe-metrics projection | `bash config/scripts/__tests__/compute-swe-metrics-projection.test.sh` | 36/36 PASS |
| Bash test: read-sub-state-metrics narrow contract | `bash config/scripts/__tests__/read-sub-state-metrics.test.sh` | 13/13 PASS |
| **Integration test** | `bash config/tests/test-metrics-pipeline-integration.sh` | **50 passed, 4 FAILED** — see Important finding below |
| Line count: compute-swe-metrics.sh | `wc -l scripts/inline/compute-swe-metrics.sh` | 57 lines (budget: <80) — PASS |
| Line count: read-sub-state-metrics.sh | `wc -l config/scripts/read-sub-state-metrics.sh` | 39 lines (budget: <50) — PASS |
| No hybrid JSONL in compute-swe-metrics.sh | `grep -E "jq|\.claude/projects|git log|tasks\.md"` | 0 matches (comment only) — PASS |
| No hybrid JSONL in read-sub-state-metrics.sh | same pattern | 0 matches — PASS |
| T-18 stale path cleanup | `grep -rn "config/scripts/compute-swe-metrics.sh" config/` | 0 matches (verify-all.sh references are a grep-for-stale check, not stale refs) — PASS |
| _complete-phase.yaml step order | manual inspect | correct: mark → ingest → compute — PASS |
| _complete-phase-spike.yaml unchanged | `git diff main -- config/workflows/_complete-phase-spike.yaml` | empty diff — PASS |
| Quarantine events | state.yaml inspection | absent — PASS |
| Git commit format | `git log --oneline main..HEAD` | 22 commits; 21 follow `feat(...): T-N <title>`, 1 follows `fix(...): <NFR-3 format>` — PASS |

---

## Dimension Scores

| Dimension | Score | Reasoning |
|-----------|-------|-----------|
| spec_compliance | 7/10 | AC-1 fails: `pass_at_1`, `pass_at_2`, `regression_rate` are marked R in metrics-schema.md variants table for feature+bugfix but arrive as NULL from the real ingest pipeline. Fields exist in the JSON envelope but are unpopulated. |
| correctness | 7/10 | `ingest-feature-metrics.py::compute_retries()` returns only `retries_total` and `human_interventions`. Design (design.md §Components #4 sketch) explicitly includes `pass_at_1`, `pass_at_2`, `regressions`, `human_interventions` in the retries return dict. The T-8 test masked this by seeding `feature_metrics` directly rather than exercising the ingest path. |
| security | 9/10 | Slug guard applied at every DuckDB entry point (`upsert_step_event`, `upsert_feature_metrics`, `upsert_synthetic_event`, `aggregate_feature`). All SQL is parameterised. No string interpolation of user data. No hardcoded secrets. |
| simplicity | 9/10 | Wrappers are 57 and 39 lines — both 70%+ under their design-commit budgets. No premature abstractions. `metrics_report.py` is a single-responsibility composition layer (~420 lines, well-structured). No dead code. |
| code_quality | 9/10 | Follows project conventions throughout. No DRY violations — `_totals()` reused from `cost_report.py`. `per_agent_tokens` and `per_agent_tools` remain stringified JSON scalars per NFR. `${TMPDIR:-/tmp}` fallback used correctly in both wrappers. Commit history is clean. |

**Overall (min of dimensions): 7/10**

No +1 bonus: integration test reports 4 failures; "every artifact exceeds minimums" is not met.

---

## AC Verification

| AC | Criterion (summary) | Status | Evidence |
|----|---------------------|--------|---------|
| AC-1 | `orchestrator metrics` returns every R field for feature schema | PARTIAL | `test-orchestrator-metrics-json-shape.sh` passes 50/50 (seeds `feature_metrics` directly). Integration test (`test-metrics-pipeline-integration.sh`) fails 4/54: `pass_at_1`, `pass_at_2`, `regressions`, `regression_rate` are NULL from real ingest path. |
| AC-2 | `compute-swe-metrics.sh` rewrite byte-compat | PASS | `compute-swe-metrics-projection.test.sh` 36/36. No JSONL, no git log in script (verified by grep). `metrics.source: duckdb@<ts>` present. |
| AC-3 | Missing tasks.md → `ingest-feature-metrics` exits non-zero | PASS | `test-ingest-feature-metrics.sh` explicitly tests this path: "PASS: missing tasks.md causes non-zero exit (fail-loud)". |
| AC-4 | `test-complete-phase-order.sh` asserts `mark < ingest < compute` | PASS | 13/13 assertions pass. Positions: mark=3, ingest=4, metrics=5. |
| AC-5 | `register-repo.sh` skips step_history rows with missing tokens | PASS | `test-register-repo-usage-invariant.sh` 4/4: only valid row inserted, warning emitted to stderr. |
| AC-6 | `orchestrator cost --format json` totals include `cache_creation_input_tokens` and `turns` | PASS | Verified via seeded DuckDB test: `cache_creation_input_tokens=20`, `turns=5` returned correctly from `aggregate_feature()`. |

---

## Findings

### Important (cap: 7)

**FINDING-1** [MUST FIX] `scripts/inline/ingest-feature-metrics.py:93–111` — `compute_retries()` omits `pass_at_1`, `pass_at_2`, `regressions`, `regression_rate`

- **What**: The function returns `{retries_total, human_interventions}` only. The design spec (design.md §4, sketch line ~326) lists `{retries_total, pass_at_1, pass_at_2, regressions, human_interventions}` in the retries return dict. `metrics-schema.md` variants table marks `pass_at_1`, `pass_at_2`, `regression_rate` as **R** (required, present with real value) for feature and bugfix schemas. The `feature_metrics` DDL has columns for all four, and `aggregate_metrics()` propagates them from `feature_metrics`; the gap is that the ingest step never writes them.
- **Why it matters**: AC-1 fails for the real ingest pipeline. Any completed feature will have NULL for these 3–4 fields in its snapshot. The T-8 test masked this by seeding `feature_metrics` directly (bypassing ingest), and the per-unit test for T-10 did not assert these specific values.
- **Evidence**: `test-metrics-pipeline-integration.sh` output (50 passed, 4 failed), SCOPE-MISMATCH FINDINGS section.
- **Fix**: Expand `compute_retries()` to derive `pass_at_1`, `pass_at_2`, `regressions`, and `regression_rate` from `state.yaml` retries data (per metrics-schema.md description: "state.yaml retries"). Add assertions for these fields in the integration test.

### Minor

**FINDING-2** [SUGGESTION] `scripts/inline/ingest-feature-metrics.py:148–151` — `rework_commits` regex matches `fix:` but the legacy `compute-swe-metrics.sh` NFR-3 fix commit `967502b` shows the workaround was applied. The NFR-3 fix commit message itself starts with `fix(ingest-feature-metrics):` which would be counted as a rework commit — minor double-counting risk, not a blocking issue.

**FINDING-3** [SUGGESTION] `config/scripts/__tests__/compute-swe-metrics.test.sh` — T-18 fixed the path from `config/scripts/compute-swe-metrics.sh` to `scripts/inline/compute-swe-metrics.sh`. The script was fixed but was not verified to produce a passing exit code against a real fixture in CI (the test uses a mock `orchestrator` binary). Not blocking — the test does exercise the path assertion.

---

## Retro-Level Items (not scored against this phase)

### ISSUE-33: `dispatch.py._find_completed_step` does not honor `repeat_until` when completed entries exist

The implementation worked around this by setting `tasks_path` in state.yaml and pruning phantom `step_history` entries. This is an orchestrator-infrastructure bug in the dispatcher, not introduced by this feature. Log for retro; needs a separate fix task.

### `register-repo.test.sh` T-5b — 2 assertions test pre-FR-11 buggy behavior

Two assertions in `config/scripts/__tests__/register-repo.test.sh` (T-5b subtest) assert that rows with missing usage are silently accepted — exactly what FR-11 now correctly rejects. These are testing the old broken behavior; they are not regressions introduced by this feature. A post-merge task should update these 2 assertions to assert the new invariant behavior (warning emitted, row skipped).

---

## Required Fix Task

### T-21: Populate `pass_at_1`, `pass_at_2`, `regressions`, `regression_rate` in `compute_retries()`

**What**: Expand `ingest-feature-metrics.py::compute_retries()` to compute the four missing fields from `state.yaml` retries data, as documented in metrics-schema.md ("state.yaml retries") and design.md §4.

**Files to modify**:
- `scripts/inline/ingest-feature-metrics.py` — expand `compute_retries()` return dict
- `config/scripts/__tests__/test-ingest-feature-metrics.sh` — add assertions for the new fields

**Verify**:
- `bash config/tests/test-metrics-pipeline-integration.sh` reports 0 failures (down from 4)
- All SCOPE-MISMATCH FINDINGS resolved
- `pass_at_1`, `pass_at_2`, `regression_rate` appear as non-null values in `orchestrator metrics --format json` output for a feature with state.yaml retries data

**depends**: T-10 (completed)

---

## Positive Observations

- Wrappers came in significantly under budget (57 lines vs <80; 39 lines vs <50).
- Zero hybrid JSONL reads in either wrapper — the iter-2 ISSUE-32 problem is fully eliminated.
- Slug guard consistently applied at all DuckDB entry points.
- All SQL parameterised; no f-string interpolation of user data.
- `_complete-phase-spike.yaml` untouched — spike contract preserved.
- 22 commits follow the required `feat(...): T-N <title>` or `fix(...): <description>` format.
- `per_agent_tokens` and `per_agent_tools` remain stringified JSON scalars as required by register-repo.sh.
- Migration is idempotent: `IF NOT EXISTS` DDL + column-presence migration guard for `turns`.
- Byte-compat test passes: 36/36 for `compute-swe-metrics-projection.test.sh`.

---

## Verdict

**approved_with_changes** — Score: 7/10

One important finding (FINDING-1) requires a fix task before final merge: `compute_retries()` must be expanded to populate `pass_at_1`, `pass_at_2`, `regressions`, and `regression_rate` from state.yaml data. All other checks pass. The core architecture — DuckDB as single source, parameterised SQL, thin wrappers, correct step ordering — is sound.

Generate T-21 and re-run the integration test after the fix. No other tasks required.
