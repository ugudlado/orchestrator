# Phase Review: ORC-119 implement phase

**Verdict:** incomplete_phase
**Date:** 2026-06-02
**Reviewer:** run-phase-review (reviewer agent)

---

## Summary

T-1 and T-2 are completed and correct. T-3 (`status: pending`) blocks phase completion.
T-3's verify command (`pytest orchestrator_next/tests/ -q`) cannot pass because 10 pre-existing
test failures exist in the baseline — none introduced by ORC-119. The gate was unsatisfiable
as written.

---

## Pending Tasks

- **T-3** (`status: pending`) — "Phase gate — full suite green"

---

## Baseline Verification

Pre-existing failures confirmed by stash comparison (ORC-119 diff = 0 new failures):

```
FAILED orchestrator_next/tests/test_dispatch_no_path3.py::test_all_steps_done_exits_1_no_json
FAILED orchestrator_next/tests/test_dispatch_retry_storm.py::test_completed_entry_in_step_history_makes_next_ready_node_skip_that_node_on_legacy_active_plan
FAILED orchestrator_next/tests/test_dispatch_retry_storm.py::test_completed_entry_in_step_history_makes_next_ready_node_skip_that_node_on_promoted_nodes_plan
FAILED orchestrator_next/tests/test_dispatch_retry_storm.py::test_orc84_fixture_with_storm_then_completion_then_more_storm_next_ready_node_skips_completed_step_id
FAILED orchestrator_next/tests/test_readiness.py::test_legacy_active_plan_advances_to_successor
FAILED orchestrator_next/tests/test_readiness.py::test_legacy_active_plan_infers_completion_from_step_history
FAILED orchestrator_next/tests/test_rework_reentry.py::TestEffectiveNodeStatus::test_recovered_history_infers_completed
FAILED orchestrator_next/tests/test_step_runner.py::test_capture_test_baseline_script_uses_step_dir_env
FAILED orchestrator_next/tests/test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[bugfix-ticket-qa]
FAILED orchestrator_next/tests/test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[feature-ticket-qa]
```

ORC-119 changes: `readiness.py` (T-1 guard) + `tests/test_rework_loop.py` (T-2 new class).
Pre-existing suite: 10 failures existed BEFORE and AFTER ORC-119 commits. Diff is empty.

**T-3's verify gate (`pytest orchestrator_next/tests/ -q`) requires a green full suite.**
The baseline is already red. T-3 must be narrowed to the ORC-119 scope.

---

## Implementation Review

### T-1: `_effective_node_status` guard

**Evidence:**

```python
def _effective_node_status(state: State, node: dict[str, Any]) -> str:
    status = node.get("status")
    if status == "completed":
        return "completed"
    if status == "pending":          # <-- T-1 guard
        return "pending"
    if _step_completed_in_history(state, _node_id(node)):
        return "completed"
    return str(status or "pending")
```

**Result:** Correct and surgical. The guard keys on the literal string `"pending"` only
(not absent/None), preserving the resume override for other statuses. ORC-119 introduced
zero new regressions — confirmed by baseline diff.

The 7 pre-existing failures in `test_readiness.py`, `test_dispatch_retry_storm.py`, and
`test_rework_reentry.py` involve nodes with `status: "pending"` + completed step_history,
but those tests were ALREADY FAILING before ORC-119 (pre-existing contradictions in the
test suite, not caused by this change).

### T-2: `TestOnFailureResetReadiness` regression tests

**Evidence:**
```
pytest orchestrator_next/tests/test_rework_loop.py -v
21 passed in 0.09s
```

All 21 tests pass including the 3 new `TestOnFailureResetReadiness` tests:
- `test_next_ready_node_prefers_reset_target_with_explicit_pending` ✓
- `test_record_needs_work_requeues_execute_next_task` ✓
- `test_completed_history_without_explicit_pending_stays_completed` ✓

---

## AC Verification

- **AC-1:** Given a state where reset target has `.status: pending` AND prior `completed`
  step_history → `next_ready_node` returns reset target.
  **PASS** — `test_next_ready_node_prefers_reset_target_with_explicit_pending` ✓

- **AC-2:** Given `record()` fails run-phase-review with `execute-next-task` having stale
  completed step_history → `next_ready_node` resolves to `execute-next-task`.
  **PASS** — `test_record_needs_work_requeues_execute_next_task` ✓

- **AC-3:** Node with completed step_history but NO explicit pending write stays completed.
  **PASS** — `test_completed_history_without_explicit_pending_stays_completed` ✓

- **AC-4:** Retry cap exhausted → no node re-opened, halt path unaffected.
  **PASS** — existing `TestReworkRecordEscalation` tests (unchanged, still green) ✓

- **AC-5:** Full `tests/test_rework_loop.py` suite continues to pass.
  **PASS** — 21/21 passed ✓

---

## Findings

### F-1 (CRITICAL — blocks phase): T-3 verify gate unsatisfiable on current baseline

- **Dimension:** spec_compliance, correctness
- **Scope:** `spec/changes/orc-119/tasks.yaml` T-3, `orchestrator_next/tests/` baseline
- **Finding:** T-3 verify command `pytest orchestrator_next/tests/ -q` requires full suite
  green. Baseline has 10 pre-existing failures. ORC-119 introduced no new failures, but
  T-3 as written cannot be satisfied without fixing pre-existing failures that are out of
  ORC-119 scope.
- **Fix direction:** Narrow T-3's verify to the ORC-119-scoped test file:
  `pytest orchestrator_next/tests/test_rework_loop.py -v`. The phase gate intent
  (no regression from ORC-119 changes) is already satisfied by that targeted run.

---

## Fixture Dirt Check

```
git diff HEAD -- tests/fixtures/
(no output — no fixture mutations)
```

---

## Baseline Comparison

No archived state.yaml entries with `metrics.review_score_avg` found — baseline comparison skipped.
