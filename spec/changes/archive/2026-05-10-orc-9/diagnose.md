# ORC-9 Diagnosis: `repeat_until` predicate ignored in dispatch loop

## Symptom

When a workflow step is declared with `repeat_until: all_tasks_completed` (e.g., `execute-next-task`), `orchestrator next` advances to the following step after the first task completes — even when unchecked tasks remain in `tasks.md`. The agent that processes each task gets dispatched only once instead of iterating.

## Reproduction

The following runnable script demonstrates the two failure modes in isolation.

### Setup

```bash
#!/usr/bin/env bash
# repro-orc-9.sh — runnable from /Users/spidey/code/orchestrator
set -euo pipefail

SCRIPTS="config/scripts/orchestrator_next"
python -m pytest "$SCRIPTS/tests/test_repeat_until.py::TestRepeatUntil::test_repeats_when_unchecked_tasks_present" -v
python -m pytest "$SCRIPTS/tests/test_dispatch.py::test_dispatch_repeats_step_when_predicate_false" -v
```

Run from the repo root:
```
cd /Users/spidey/code/orchestrator
bash repro-orc-9.sh
```

### Expected output (pre-fix, RED)

```
FAILED tests/test_repeat_until.py::TestRepeatUntil::test_repeats_when_unchecked_tasks_present
  AssertionError: Expected next_step='execute-next-task' (repeat),
  got 'run-phase-review'. Bug: _compute_next_step advanced past the step
  despite tasks.md containing unchecked items.

FAILED tests/test_dispatch.py::test_dispatch_repeats_step_when_predicate_false
  AssertionError: dispatch() must re-emit 'execute-next-task' while unchecked tasks
  remain (repeat_until: all_tasks_completed). Got step_id='run-phase-review'.
  Bug: dispatch.py history-walk ignores contract.repeat_until and advances to
  'run-phase-review' prematurely.
```

### Actual output (post-fix, GREEN — current main)

```
PASSED tests/test_repeat_until.py::TestRepeatUntil::test_repeats_when_unchecked_tasks_present
PASSED tests/test_dispatch.py::test_dispatch_repeats_step_when_predicate_false
```

**The fix is already present on main.** See "Root cause" and "Fix status" below.

## Root Cause

Two independent seams both needed the `repeat_until` predicate check. The bug existed in both; they were fixed separately.

### Seam 1 — `record.py::_compute_next_step` (ISSUE-16, fixed first)

**File:** `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`

When a step is recorded as completed via `orchestrator done`, `_compute_next_step` decides what `next_step` to write into `state.yaml`. The original code naively advanced through `workflow_plan[phase].active[]` without consulting the step's `repeat_until` contract field.

**Divergence point (pre-fix):** `record.py`, `_compute_next_step` function. The loop:

```python
for sid in active:
    if (phase, sid) not in completed:
        return {"phase": phase, "step_id": sid}
return None
```

After marking `execute-next-task` completed, `(phase, "execute-next-task")` is in `completed`, so the loop immediately returned `run-phase-review` — never consulting `repeat_until`.

**Fix applied:** `record.py` now calls `load_contract_for_step(just_completed_step_id)`, reads `contract.repeat_until`, looks up the predicate in `REPEAT_PREDICATES`, and if the predicate returns `False` (unchecked tasks remain), returns `{"phase": phase, "step_id": just_completed_step_id}` — re-emitting the same step.

Current fix location: `record.py` lines 961–974.

### Seam 2 — `dispatch.py` history-walk loop (fixed in T-2.5 / HL-303)

**File:** `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/dispatch.py`

When `orchestrator next` is called, `dispatch()` independently walks `step_ids` (from `workflow_plan[phase].active`) to find the next pending step. `_find_completed_step` returns `True` for any step with a `completed` history entry. The original loop:

```python
for sid in step_ids:
    if not _find_completed_step(state.step_history, state.phase, sid):
        next_step_id = sid
        break
```

**Divergence point:** `dispatch.py` line 310 (pre-fix version). After `execute-next-task` completed its first run, `_find_completed_step` returned `True` and the loop continued to `run-phase-review` — without ever consulting `contract.repeat_until` or evaluating `_check_all_tasks_completed`.

`_find_completed_step` itself is not wrong — it correctly reports whether any completed entry exists. The bug is in the **caller**: the loop that uses its result never checks whether that "completed" step should loop again.

**Fix applied:** After `_find_completed_step` returns `True`, the dispatch loop now also loads the contract, checks `sid_contract.repeat_until`, evaluates the named predicate against `state.raw`, and re-emits the step if the predicate returns `False`.

Current fix location: `dispatch.py` lines 313–323.

```python
# Step is marked completed — but if its contract declares repeat_until,
# evaluate the predicate. If False, re-emit this step (don't advance).
try:
    sid_contract = load_contract_for_step(sid, state_yaml_path)
except (FileNotFoundError, ContractError):
    sid_contract = None
if sid_contract is not None and sid_contract.repeat_until:
    predicate = REPEAT_PREDICATES.get(sid_contract.repeat_until)
    if predicate is not None and not predicate(state.raw):
        next_step_id = sid
        break
```

### Why two seams?

`record.py` and `dispatch.py` both compute "what is the next step?" independently:

- `record.py::_compute_next_step` writes `next_step` to `state.yaml` at record time (used by the driver loop's UI / progress display).
- `dispatch.py::dispatch` re-derives the next step at dispatch time from the live step_history (the authoritative path for what actually gets dispatched).

Both seams must honor `repeat_until`. A fix to only one seam would cause `state.yaml.next_step` and the actual dispatch action to diverge, leading to inconsistent behavior.

## Impact

**Callers of `_find_completed_step` (dispatch.py only):**
- `_find_completed_step` is called in one location: `dispatch.py` line 310 (the history-walk loop). No other callers.

**Callers of `_compute_next_step` (record.py only):**
- `_compute_next_step` is called in one location: `record.py` line 1215 inside `record()`. No other callers.

**Affected step contracts with `repeat_until`:**
- `/Users/spidey/.config/orchestrator/config/steps/execute-next-task.yaml` — `repeat_until: all_tasks_completed`
- Only one registered predicate: `REPEAT_PREDICATES = {"all_tasks_completed": _check_all_tasks_completed}` (`record.py` line 939–941)

**Test coverage (pre-fix):**
- `test_repeat_until.py` covered `_compute_next_step` (record.py seam) — 3 tests.
- `test_dispatch.py::test_dispatch_repeats_step_when_predicate_false` covered `dispatch()` (dispatch.py seam) — 1 test.
- All 4 tests were written as RED (failing) regression tests and now PASS on main.

## Fix Status

**Both seams are already fixed on main** as of commit `7bdd31c` (fix(hl-303): T-2.5 dispatch.py honors repeat_until via shared REPEAT_PREDICATES). The fix predates the ORC-9 workflow creation.

The current `orc/orc-9` branch is at the same commit as `main` (HEAD: `639caf1`).

All 4 regression tests pass:
```
config/scripts/orchestrator_next/tests/test_dispatch.py::test_dispatch_repeats_step_when_predicate_false PASSED
config/scripts/orchestrator_next/tests/test_repeat_until.py::TestRepeatUntil::test_repeats_when_unchecked_tasks_present PASSED
config/scripts/orchestrator_next/tests/test_repeat_until.py::TestRepeatUntil::test_advances_when_all_tasks_checked PASSED
config/scripts/orchestrator_next/tests/test_repeat_until.py::TestRepeatUntil::test_no_repeat_when_contract_lacks_repeat_until PASSED
```

## Proposed Approach

No implementation work is required — the fix is already merged to main.

The scope for ORC-9 should either be:
1. **Confirm and close**: verify both seams are correctly fixed, confirm test coverage, archive.
2. **Harden coverage**: identify any remaining edge cases (unknown predicate name, missing contract file, tasks.md unreadable) and add tests for those paths if they lack coverage.

## Unresolved Questions

1. **Was ORC-9 created before HL-303 merged?** The workflow was initialized on `2026-05-09T21:09:29Z`; commit `7bdd31c` (the fix) is dated `2026-05-04`. This suggests ORC-9 was opened after the fix landed — possibly to independently verify it, or to formalize the bugfix as a tracked workflow.

2. **Edge case coverage for `_check_all_tasks_completed`:** The fail-closed path (path is constructible but file is missing → returns `False`) prevents silent skip, but there is no test for this case. Is it in scope for ORC-9 to add it?

3. **Unknown predicate names:** If `repeat_until` names a predicate not in `REPEAT_PREDICATES`, `dispatch.py` silently skips the re-emit (the predicate lookup returns `None`, the `if predicate is not None` guard short-circuits). `record.py` emits a stderr warning but also skips. Should an unknown predicate be a hard error? No existing test covers this path.
