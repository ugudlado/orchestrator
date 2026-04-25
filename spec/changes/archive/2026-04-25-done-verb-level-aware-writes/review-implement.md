# Phase Review: done-verb-level-aware-writes — implement

**Date**: 2026-04-25  
**Reviewer**: reviewer agent (claude-sonnet-4-6)  
**Worktree**: `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes`  
**Branch**: `feature/done-verb-level-aware-writes`  
**Commits on branch**: 27 (from brief); 394 total including merge history

---

## Dimension Scores

| Dimension | Score | Key Findings |
|-----------|-------|--------------|
| spec_compliance | 9 | All 11 ACs pass; 1 minor deviation (record.py:1037 usage string not updated per design.md directive) |
| correctness | 9 | All test files pass; rollback test verifies step_events absence; _detect_boundary behaves correctly |
| security | 9 | All SQL parameterized; slug guard in upsert.py; no string interpolation in SQL paths |
| simplicity | 9 | Three-stage migration is clean; helpers are small and focused; no dead code |
| code_quality | 9 | Consistent patterns; NFR-5 via upsert.py slug guard; design patterns followed |
| **Overall** | **9** | min(9,9,9,9,9) |

---

## Verification Gates

### Test Suite
```
pytest config/scripts/orchestrator_next/tests/ -q
→ 288 passed, 2 failed (pre-existing), 11 warnings in 4.65s
```
The 2 failures (`test_archive_backlog_cleanup.py::test_backlog_dir_removed_after_archive`, `test_cleanup_commit_in_git_log`) are confirmed pre-existing:
- Same failures on `main` branch (verified independently)
- `git log -- config/scripts/orchestrator_next/tests/test_archive_backlog_cleanup.py` shows this file predates the branch

### m8-gates.sh
```
bash scripts/m8-gates.sh → "All M8 gates PASS"
```
Note: Gate 5 uses `python3 -m unittest discover -s config/scripts/tests 2>&1 | tail -3` — the `| tail` masks the exit code of unittest; 28 failures/6 errors in the old test suite are pre-existing (confirmed identical on `main` branch). Gate 5 does not exit the script on failure by design; this is a pre-existing gate limitation.

### Banner
```
python bin/orchestrator
→ Usage:
    orchestrator next <state.yaml>
    orchestrator done <state.yaml>   # JSON payload on stdin
    orchestrator doctor
```
PASS: Banner shows `done`, no `record`, no `ingest-driver`, no `ingest-subagents`.

### Pre-existing failure confirmation
```
# On main branch
pytest config/scripts/orchestrator_next/tests/test_archive_backlog_cleanup.py -q
→ 2 failed, 1 passed
# git log shows test file was added in commit predating this branch
```

---

## AC Verification Table

| AC | Claim | Verify Command | Result | Evidence |
|----|-------|---------------|--------|---------|
| AC-1 | `orchestrator record` continues to work after Stage A | `python bin/orchestrator record /tmp/x.yaml <<< '{}'` | PASS | Routes to record_main, returns expected JSON (payload_missing_keys error), not Usage banner |
| AC-2 | Phase-boundary step writes step_events + phase_events | `pytest test_phase_boundary_write.py::test_atomic_commit_phase_boundary` | PASS | Test seeds step + asserts 1 row each in step_events and phase_events |
| AC-3 | Non-boundary step writes only step_events | `pytest test_done_status_dispatch.py::test_completed_writes_step_events_row` | PASS | phase_events count = 0 confirmed |
| AC-4 | `status: recovered` skips boundary even on last step | `pytest test_done_status_dispatch.py::test_recovered_writes_step_with_recovered_status` | PASS | step_events has status=recovered; phase_events count = 0 |
| AC-5 | `status: abandoned` writes step row + sets state.yaml.status=blocked | `pytest test_done_status_dispatch.py::test_abandoned_sets_state_blocked test_abandoned_writes_step_with_abandoned_status` | PASS | Two distinct tests; both assert correct row and state mutation |
| AC-6 | Feature-boundary step writes step+phase+driver_sessions | `pytest test_phase_boundary_write.py::test_atomic_commit_feature_boundary` | PASS | All three table rows confirmed by SELECT COUNT |
| AC-6a | Sub-agent rows inserted at feature boundary; agent_report view returns per-subagent rows | `pytest test_subagent_absorption.py -q` (all 9 tests including test_agent_report_view_after_subagent_write) | PASS | test (h) explicitly queries `SELECT agent_name FROM agent_report WHERE change_id=?` and asserts per-agent rows |
| AC-7 | Boundary write failure: non-zero exit AND no step_events row left | `pytest test_phase_boundary_write.py::test_rollback_on_failure` | PASS | Test asserts code \!= 0 AND `SELECT COUNT(*) FROM step_events = 0` after ROLLBACK |
| AC-8 | m8-gates.sh asserts `orchestrator done` in banner | `bash scripts/m8-gates.sh` | PASS | Gate 6 passes; `grep -q "orchestrator done"` succeeds and `grep -q "orchestrator record"` fails (banner clean) |
| AC-9 | Migration 0003 creates phase_events + driver_sessions; recorded in schema_migrations | `python3 -c "from orchestrator_next.upsert import ensure_schema; ...ensure_schema(db); ensure_schema(db);"` | PASS | Both tables created with correct columns; schema_migrations has exactly 1 row for 0003; second call is no-op |
| AC-10 | _complete-phase.yaml has no ingest-auto steps; bin/orchestrator banner free of ingest verbs | `grep -n "ingest-driver\|ingest-subagents" config/workflows/_complete-phase.yaml` (exit 1 = not found) + `python bin/orchestrator` banner check | PASS | grep returns nothing (exit 1); banner shows only `next`, `done`, `doctor`; files deleted confirmed by `ls` |

**AC Summary: 11 passed (AC-1 through AC-10 plus AC-6a), 0 failed.**

---

## Quarantined Tasks

None. `state.yaml` has no `quarantine_events` key.

---

## Baseline Comparison (Step 5b)

Archives with `schema: feature` and `metrics.review_score_avg`:

| Archive | review_score_avg |
|---------|-----------------|
| 2026-04-17-duckdb-ingest-normalized-metrics-tables | 9.0 |
| 2026-04-17-cross-repo-metrics-duckdb | 8.3 |
| 2026-04-12-hl-278 | 9.0 |
| 2026-04-19-single-source-metrics-via-step-events | 0 |
| 2026-04-17-learn-and-telemetry-on-duckdb | 9.0 |
| 2026-04-17-metrics-capture-and-workflow-streamlining | 9.0 |
| 2026-04-11-hl-276 | 9.5 |
| **Average** | **7.69** |

Note: The 0 entry from `single-source-metrics-via-step-events` heavily depresses this average. Excluding it: average = 9.0. Current score of 9 matches or exceeds the historical average. **No regression warning.**

---

## Caller-Site Spot-Check (Cycle-16 Rule)

1. **design.md claim**: `bin/orchestrator:84,170` for `_compute_cost_usd` import  
   **Reality**: Those line numbers are stale post-Stage-C deletion (213 lines removed). `_compute_cost_usd` is NOT in `bin/orchestrator` (grep returns empty). It lives in `record.py` at line 472. The design acknowledges this migration explicitly.  
   **Finding**: Line numbers in design.md are stale but the migration intent was correct and fully implemented. Minor informational note only.

2. **design.md claim**: `skills/orchestrate/SKILL.md` has "4 occurrences" (lines 88, 135, 139, 174); spec.md says "3 dispatch references on lines 88, 135, 139, 174" (lists 4 lines). Brief says "6 occurrences".  
   **Reality**: `grep -c "orchestrator done" skills/orchestrate/SKILL.md` returns 6, `grep -c "orchestrator record"` returns 0.  
   **Finding**: Spec/design count discrepancy (3 vs 4 vs 6), but all occurrences are correctly updated to `done`. No production error.

3. **design.md directive**: "Update CLI usage string in `main()` to advertise `done` instead of `record`."  
   **Reality**: `record.py:1037` still says `"Usage: orchestrator record <state.yaml>  (JSON payload on stdin)"`.  
   **Finding**: This string is **never user-visible** in normal usage — `bin/orchestrator` intercepts `done <missing-state.yaml>` at line 121-122 and calls `_usage()` instead of reaching `record_main` with < 2 args. The path via `record_main(["done"])` requires bypassing `bin/orchestrator` entirely. Cosmetic drift from design directive; zero functional impact.

---

## Findings Summary

### Critical Findings: 0

### Important Findings: 0

### Minor Findings (informational, no cap impact)

1. **[MINOR]** `config/scripts/orchestrator_next/record.py:1037` — Usage string still says `"orchestrator record"` instead of `"orchestrator done"`. Design.md directive was not followed. Not user-visible in any normal code path (bin/orchestrator intercepts before this is reached). Fix: change the string to `"orchestrator done"`.

2. **[MINOR]** `record.py:522` — `datetime.datetime.utcnow()` is deprecated. Generates 11 DeprecationWarnings in the test suite. Not new to this feature but surfaces in the new test files.

3. **[INFORMATIONAL]** Gate 5 in `scripts/m8-gates.sh` uses `| tail -3` which masks the exit code of the old `config/scripts/tests` unittest suite. The 28 failures/6 errors in that suite are pre-existing and unrelated to this feature. Gate reports PASS correctly because it's checking a different assertion.

---

## Score Summary

| Dimension | Score |
|-----------|-------|
| spec_compliance | 9 |
| correctness | 9 |
| security | 9 |
| simplicity | 9 |
| code_quality | 9 |
| **Overall** | **9** |

**Minimum = 9. No critical or important findings. No first-pass +1 (retries.run-phase-review: 1 consumed it).**

---

## Verdict: PASS

Overall score 9 >= `review_score.min: 9`. All 11 ACs verified with evidence. No critical findings. No quarantined tasks. No quality regression vs baseline.

---

## Evidence Summary

- `pytest config/scripts/orchestrator_next/tests/ -q` → 288 passed, 2 pre-existing failures
- `bash scripts/m8-gates.sh` → All M8 gates PASS  
- `python bin/orchestrator` → banner shows only `done`, no `record`/`ingest-*`
- `python bin/orchestrator ingest-driver` → exit 3, Usage banner (not dispatched)
- `python bin/orchestrator record /tmp/x.yaml <<< '{}'` → routes to record_main (backward compat confirmed)
- `grep -rn "ingest-driver-auto|ingest-subagents-auto" config/ scripts/` → 0 hits in step/workflow files; only in test fixtures and test comment annotations
- Migration 0003 idempotency: apply twice, no error, single schema_migrations row
- AC-6a agent_report test: `SELECT agent_name FROM agent_report WHERE change_id=?` returns per-subagent rows after `_write_subagent_events`
- AC-7 ROLLBACK: `SELECT COUNT(*) FROM step_events = 0` after mocked `_write_phase_event` raises
