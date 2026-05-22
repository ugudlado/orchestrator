# Tasks — Implement run-phase-review needs_work rework loop

- [x] T-1: Reconcile error-recovery.md row 17 with run-phase-review.yaml step 7b (no RED — contract-doc change)
  Why: AC-8, Decision OQ-1 — the two contracts disagree on the terminal status of a needs_work review; the engine logic in T-3/T-5 depends on the reconciled wording.
  Files: config/steps/contracts/error-recovery.md
  Change: In the "Phase verification failed" row (row 17) replace `status: failed` / `retries.phase_verify` with `status: completed` + `verdict: needs_work` and `retries.run-phase-review`; grep the rest of error-recovery.md for any sibling `phase_verify` reference and fix it for consistency.
  Test scenarios:
    - regression guard: `grep -n "verdict: needs_work" config/steps/contracts/error-recovery.md` matches row 17
    - regression guard: `grep -c "phase_verify" config/steps/contracts/error-recovery.md` returns 0
    - run-phase-review.yaml step 7b and error-recovery.md row 17 agree on terminal status (completed) and retry key (run-phase-review)

- [ ] T-2: Write tests for verdict extraction + rework-decision helpers (RED — tests must fail)
  Why: AC-1, AC-2, AC-5, AC-7 — the helpers `_phase_review_verdict`, `_rework_loop_active`, and `_max_retry_rounds` do not exist yet.
  Files: config/scripts/orchestrator_next/tests/test_rework_loop.py
  Change: New test module asserting the three pure helpers; tests fail today because the symbols are unimported/undefined.
  Test scenarios:
    - `_phase_review_verdict` returns the verdict for a run-phase-review payload, None for other step ids, None when phase_review_report is absent
    - `_rework_loop_active` returns "retry" for needs_work / incomplete_phase below ceiling, "escalate" at/above ceiling, None for pass
    - `_max_retry_rounds` reads quality_bar.max_retry_rounds from project.yaml, returns 3 (with stderr warning) when the key is absent
    - retries absent or not a dict is treated as count 0 without raising

- [ ] T-3: Implement verdict extraction + rework-decision helpers (GREEN — make tests pass)
  Why: AC-1, AC-2, AC-5, AC-7, Decisions OQ-2/OQ-6 — pure decision layer the node-status branch depends on.
  Files: config/scripts/orchestrator_next/record.py
  Change: Add `_phase_review_verdict(payload)`, `_rework_loop_active(verdict, retries, max_retries)`, and `_max_retry_rounds(state_raw)` near `_validate_phase_review_output` (record.py:74); "rework verdict" set = {needs_work, incomplete_phase}; `_max_retry_rounds` reads project.yaml `quality_bar.max_retry_rounds`, default 3 with a `[record]` stderr warning.
  Test scenarios:
    - all T-2 tests pass
    - type-check / lint clean
  depends: T-2

- [ ] T-4: Write tests for node-status rework re-open and escalation (RED — tests must fail)
  Why: AC-1, AC-2, AC-3, AC-4, AC-6 — record() does not yet re-open nodes on needs_work nor escalate on ceiling.
  Files: config/scripts/orchestrator_next/tests/test_rework_loop.py
  Change: Extend the test module with end-to-end `record()` cases over a nodes-shape state.yaml fixture (and a separate legacy `active:[ids]` fixture); tests fail because record() currently advances linearly past needs_work.
  Test scenarios:
    - needs_work + retries < max: run-phase-review and execute-next-task nodes left in_progress; intermediate nodes (e.g. run-ux-critique) stay completed; `readiness.next_ready_node` returns execute-next-task
    - needs_work + retries >= max: recorded step_history entry has status blocked; state.status == "paused"; calling `dispatch.dispatch()` on the post-record state exits 2 (proves the downgrade triggers the existing exit-2 path end-to-end)
    - pass verdict: node marked completed, next_step advances to the following step (no regression)
    - incomplete_phase + retries remaining: re-dispatches execute-next-task identically to needs_work
    - needs_work with no fix tasks appended: loop re-runs run-phase-review and is bounded by max_retry_rounds
    - legacy `active:[ids]` plan fixture: needs_work record degrades to linear advance, no exception (mark_node_status is a no-op on a node-less plan)

- [ ] T-5: Implement rework-loop node re-open and ceiling escalation in record() (GREEN — make tests pass)
  Why: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, Decisions OQ-3/OQ-4/OQ-5 — the verdict-aware branch in the node-status flip block.
  Files: config/scripts/orchestrator_next/record.py
  Change: In the node-status flip block (record.py:1494-1498), when step is run-phase-review and `_rework_loop_active` returns "retry", leave run-phase-review `in_progress` and reset only the `execute-next-task` node to `in_progress` via `readiness.mark_node_status` (intermediate nodes untouched); when it returns "escalate", set the recorded entry `status` to `blocked` before append and set `state_raw["status"] = "paused"`; "pass"/None keeps existing behavior.
  Test scenarios:
    - all T-4 tests pass
    - `test_repeat_until.py`, `test_readiness.py`, `test_dispatch_resume.py`, `test_record_validation.py` stay green (no regression)
  depends: T-3, T-4

- [ ] T-6: Review checkpoint — rework loop (phase gate)
  Why: phase gate — confirm the contract edit and engine change integrate cleanly and the full suite is green before phase review.
  Test scenarios:
    - type-check / lint clean
    - `pytest config/scripts/orchestrator_next/tests/ -q` fully green
    - error-recovery.md row 17 and run-phase-review.yaml step 7b are consistent (T-1 regression greps still pass)
  depends: T-1, T-5

<!-- Format contract: contracts/artifact-formats.md § Task Format Contract -->
<!-- TDD: T-1 is a contract-doc change (no RED — regression-guard greps as Test scenarios). -->
<!-- T-2/T-3 and T-4/T-5 are RED→GREEN pairs carried by `depends:`. -->
</content>
