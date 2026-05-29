# Phase Review: ORC-103 — needs_work rework loop re-runs phase review after fix task

- **Change:** orc-103 (feature)
- **Phase:** main (implement)
- **Attempt:** 1 (no retries this round)
- **Verdict:** **PASS**
- **Overall score:** 10 / 10
- **Reviewer:** reviewer agent
- **Date:** 2026-05-29

---

## Scoring config (from project.yaml quality_bar.scoring)

| Field | Value |
|-------|-------|
| critical_cap | 5 |
| important_cap | 7 |
| green_base | 9.25 |
| min_phase_review_score | 9 |
| max_retry_rounds | 8 |

## Verify block evaluation (contract steps 2–4)

The `run-phase-review` step contract (`config/steps/run-phase-review/contract.yaml`)
defines a `verify:` block of **output post-conditions** (report written, review_score
recorded, critical findings resolved), not `verify.commands` / `verify.assertions` /
`verify.metrics` with numeric thresholds.

- **verify.commands:** none defined at the schema/contract level. The executable
  verification is carried by the tasks' own `verify` pytest commands (evaluated below).
- **verify.assertions:** the contract's output assertions are satisfied — phase-review.md
  is written, review_score is recorded for a `pass` verdict, no unresolved critical findings.
- **verify.metrics:** **no schema-defined coverage, build, or type-check threshold for
  this step.** `tdd_required=true`, but the contract specifies no `test_coverage` metric,
  so step 4's coverage gate has nothing to evaluate. Tests pass; there is no coverage gate
  to apply.

## Test execution evidence

| Command | Result |
|---------|--------|
| `pytest config/scripts/orchestrator_next/tests/ -q` | **698 passed, 2 skipped, 1 xfailed** (exit 0) |
| `pytest .../test_rework_reentry.py -v` | **11 passed** (exit 0) |
| `pytest .../test_dispatch_retry_storm.py .../test_rework_loop.py -q` | **34 passed** (exit 0) |

Baseline at `capture-test-baseline` was 707 passed / 3 failing (the 3 RED tests T-1
authored). Post-implementation full suite is fully green — no regressions in dispatch,
readiness, rework-loop, or retry-storm surfaces.

## Pending task-node guard

All task-nodes (`task-T-1` … `task-T-5`) are `status: completed` in `workflow_plan`.
No pending task-nodes; the `incomplete_phase` guard does not fire. No `quarantine_events`
in state.yaml.

## AC verification with evidence

### AC-1 — re-review after fix; counter increments
> A re-armed `run-phase-review` node after a needs_work verdict + completed `task-fix-N`
> yields `next_ready_node == run-phase-review` (not the next declaration-order node), and
> `retries["run-phase-review"]` exceeds its pre-rework value.

- **Readiness:** `test_after_fix_task_next_ready_is_run_phase_review_not_compute` — PASS.
  With a `completed`+`needs_work` history entry on an `in_progress` review node whose
  `depends_on=[task-fix-1]`, `next_ready_node` returns `run-phase-review`, **not**
  `compute-prediction-accuracy` — the live orc-96 bug.
- **Counter (engine-owned, non-circular):**
  `test_needs_work_counter_climbs_from_engine_not_payload` — PASS. Verified the test feeds
  `_review_payload("needs_work")` with `retries_count=None` (no `state_patch.retries`), so
  the 0→1→2→3 climb is produced **solely** by the engine increment at
  `record.py:1690`. This is the discriminating test, not a self-supplied climb.
- **Evidence:** `record.py:1687-1690` increments after `_rework_loop_active` reads the
  pre-increment count (round N reads N-1, bumps to N). **PASS.**

### AC-2 — advance only on pass
> A `pass` verdict (overall ≥ min_phase_review_score) makes `run-phase-review` terminal and
> `compute-prediction-accuracy` the next ready node.

- `test_after_pass_review_next_ready_is_compute` — PASS. With a trailing `completed`+`pass`
  history entry, `next_ready_node` returns `compute-prediction-accuracy`.
- `test_pass_completed_history_is_terminal` and `test_multi_round_history_trailing_pass_wins`
  — PASS (earlier needs_work entries stay non-terminal; the trailing pass terminates). **PASS.**

### AC-3 — cap exhaustion blocks (exit 2)
> At `max_retry_rounds` with verdict still needs_work, the entry is `blocked`, state is
> `paused`, and the next `orchestrator next` exits 2 (no advance).

- `test_max_retries_exhaustion_blocks_pauses_dispatch_exits_2` — PASS. Drives needs_work
  rounds to the cap; on the over-cap round `record()` sets entry `status=blocked`,
  `state.status=paused`; `dispatch.dispatch()` returns `({}, 2)` — no spawn of compute.
- `test_below_cap_does_not_block_or_pause` — PASS (below cap: entry stays completed, node
  in_progress, state active). The previously-dead escalate branch is now live. **PASS.**

### AC-4 — verdict-aware effective status; crash-resume preserved
> A `completed`+needs_work entry on an `in_progress` node → `_effective_node_status` returns
> `in_progress`; a `recovered` entry (no needs_work verdict) on an `in_progress` node →
> still `completed` (ORC-85 crash-resume preserved).

- `test_needs_work_completed_history_on_in_progress_node_returns_in_progress` — PASS.
- `test_recovered_history_on_in_progress_node_returns_completed` — PASS (ORC-85 preserved;
  the 34 retry-storm/rework-loop regression tests confirm no regression).
- `test_incomplete_phase_completed_history_is_non_terminal` — PASS.
- `test_malformed_evidence_treated_as_terminal` — PASS (fail-safe: unreadable verdict →
  terminal). **PASS.**

## Implementation vs. design conformance

- `readiness._step_completed_in_history` (`readiness.py:74-85`): verdict-aware skip of
  `{needs_work, incomplete_phase}` via lazy-imported `record._phase_review_verdict` —
  exactly the chosen Approach 1, ~5 lines, lazy-import precedent (`REPEAT_PREDICATES`) honored.
- `record.py:1687-1690`: engine-owned `retries["run-phase-review"]` increment in the retry
  branch, applied after the cap read. Matches the OQ-3 decision.
- `config/steps/run-phase-review/prompt.md`: the `state_patch.retries increment` line is
  removed (grep confirms zero references) — engine is the sole incrementer, no double-count
  (which would have capped at 4 rounds instead of 8).
- `_REWORK_VERDICTS = {needs_work, incomplete_phase}` (`record.py:116`) — shared by
  `_rework_loop_active` and the readiness skip, so both layers agree on which verdicts re-arm.

## Findings

None blocking. Dimensions all green.

## Non-blocking observation (not a finding; no action required this phase)

- The end-to-end `record()`-level tests (counter climb, exhaustion) exercise only the
  `needs_work` verdict. `incomplete_phase` is verified at the readiness layer
  (`test_incomplete_phase_completed_history_is_non_terminal`) and is a member of
  `_REWORK_VERDICTS`, so `record()` re-arms on it identically — but a record()→dispatch
  end-to-end assertion for `incomplete_phase` is not present. No AC requires it; the shared
  `_REWORK_VERDICTS` set makes the two paths equivalent by construction. Noted for evidence
  fidelity, not as a gate.

## Dimension scores

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| spec_compliance | 9.25 → 10 | AC-1…AC-4 all verified with passing tests; design/tasks format-compliant; full traceability. |
| correctness | 9.25 → 10 | Implementation matches design; discriminating non-circular test confirmed; full suite green; no quarantine. |
| security | 9.25 → 10 | Internal engine control-flow; no auth/input/secret/IO surface introduced. |
| simplicity | 9.25 → 10 | Surgical S-complexity change: one predicate, one increment, one prompt-line removal. |
| code_quality | 9.25 → 10 | Lazy-import precedent, fail-safe verdict handling, clear docstrings, no TODO/FIXME/placeholder. |

**Overall = min(dimensions) = 9.25.**

First-pass bonus (+1, max 10) — all three conditions met:
- (a) Artifacts exceed minimums (8 readiness-layer + 3 record/dispatch end-to-end tests; full design rationale). ✅
- (b) No TODO/FIXME/placeholder text in outputs. ✅
- (c) All verify passed on attempt 1, no retries this round. ✅

**Overall score: 10 / 10.**

## Baseline comparison (non-blocking)

green_base was auto-adjusted to 9.25 on 2026-05-29 (historical feature avg 9.6, retry 0%).
Current overall (10) is above the historical average — no regression warning.

## Verdict

**PASS** — overall 10 ≥ min_phase_review_score (9), zero critical findings, all task-nodes
terminal, full suite green. The quality gate that ORC-103 was filed to close is now closed:
a `needs_work` verdict re-arms `run-phase-review`, re-review advances only on a passing
verdict, and the retry cap blocks (exit 2) on exhaustion — all proven end-to-end.
