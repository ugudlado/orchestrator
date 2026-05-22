# Phase Review: orc-67 — run-phase-review needs_work rework loop

**Mode:** phase-level signoff (Mode 3). Independent verification of all 6 tasks
(T-1..T-6), `feature` schema, `main` phase. Reviewer ran every check itself.

## Scores

| Dimension | Score | Key Issues |
|-----------|-------|------------|
| Spec Compliance | 9/10 | All 8 ACs verified pass with evidence. No scope creep. |
| Correctness | 9/10 | All branches (retry/escalate/pass) exercised on real `record()`. No critical/important findings. |
| Security | 9/10 | stdlib + pyyaml only; `yaml.safe_load`; no injection surface; no secrets. |
| Simplicity | 9/10 | Extends the proven `repeat_until` pattern; 3 small pure helpers; no new module/schema. |
| Code Quality | 9/10 | Named helpers, docstrings, follows existing conventions; non-colliding rename is sound. |
| **Overall** | **9/10** | **PASS** — `min_phase_review_score` (9) met. |

`overall` = MIN(dimensions) = 9. No `+1` bonus awarded: the work is solid,
correct spec execution but not "exceeds minimum requirements" exceptional —
default to 9.

## Verification

- **Test command:** `python3 -m pytest config/scripts/orchestrator_next/tests/ -q`
  → **478 passed, 5 failed** (7.72s). Confirmed independently.
- **Failure set:** byte-identical to the 5 exempt ORC-69 subprocess tests
  (`test_dispatch_no_path3`, `test_dispatch_pending_row` ×2, `test_dispatch_resume`
  ×1, `test_smoke_post_migration`). Baseline 452/5 → ORC-67 478/5 (+26 tests,
  same 5 exempt failures). **No new failure introduced.**
- **test_dispatch_resume.py** (concern #3): 39 passed / 1 failed — the single
  failure is the exempt subprocess test `test_terminal_record_after_resume_cleans_and_advances`;
  every other test in that file passes. Not a regression.
- **test_rework_loop.py:** 26 passed / 0 failed — the full new suite is green.
- **Regression suites named in T-5** (`test_repeat_until`, `test_readiness`,
  `test_record_validation`, `test_phase_boundary_write`, `test_feature_metrics_compute`):
  90 passed / 0 failed.
- **Type-check:** `python3 -m py_compile record.py test_rework_loop.py` → clean.
- **Unchecked tasks:** 0 (all 6 `[x]`). **TODO/FIXME/placeholder in diff:** 0.
- **quarantine_events:** absent (auto=false, none expected). **retries:** absent
  (0 — no retries this round). **state.status:** active.

## Acceptance Criteria — verified with evidence

| AC | Criterion | Status | Evidence |
|----|-----------|--------|----------|
| AC-1 | needs_work < ceiling → both nodes re-dispatchable, next = execute-next-task | PASS | `test_needs_work_below_ceiling_reopens_both_nodes` — run-phase-review & execute-next-task `in_progress`, run-ux-critique `completed`, `next_ready_node` = `execute-next-task` |
| AC-2 | needs_work >= ceiling → entry blocked, state paused, next exits 2 | PASS | `test_ceiling_blocks_and_pauses_and_dispatch_exits_2` — entry `status: blocked`, `state.status: paused`, `dispatch.dispatch()` exit 2 |
| AC-3 | pass verdict → unchanged linear advance | PASS | `test_pass_verdict_advances_normally` + 90 regression tests green |
| AC-4 | nodes-shape composition via `mark_node_status`; legacy `active:[ids]` degrades | PASS | `test_legacy_active_plan_degrades_without_error` (exit 0, no `nodes` key); nodes-shape tests mutate only via `readiness.mark_node_status` |
| AC-5 | incomplete_phase identical to needs_work | PASS | `test_incomplete_phase_behaves_like_needs_work` |
| AC-6 | needs_work, no fix tasks → loop bounded by max_retry_rounds | PASS | `test_needs_work_no_fix_tasks_still_bounded` — execute-next-task re-opened with fully-checked tasks.md |
| AC-7 | retries absent / non-dict → treated as 0, no raise | PASS | `test_retries_absent/none/not_a_dict_treated_as_zero` (3 tests) |
| AC-8 | error-recovery.md row 17 reconciled | PASS | `grep "verdict: needs_work"` matches row 17; `grep -c "phase_verify"` = 0; row 17 states `status: completed`, `verdict: needs_work`, `retries.run-phase-review` |

**8/8 ACs verified pass.** No failing AC → no critical finding in spec_compliance.

## Developer-flagged concerns — judged

1. **Helper naming deviation (`_payload_phase_review_verdict` vs `_phase_review_verdict`)
   — ACCEPTABLE, not a finding.** Confirmed a pre-existing `_phase_review_verdict(entry)`
   at `record.py:972` reads a `step_history` entry's `evidence.outputs` and is
   load-bearing for `extract_review_scores`. Reusing the name with the new
   payload-time signature would have broken that function. The non-colliding
   rename is sound engineering judgment; the new helper has a docstring noting
   the distinction.

2. **Escalation branch + boundary detection — NON-FINDING.** On the `escalate`
   branch the local `status` variable stays `"completed"` when passed to
   `_detect_boundary`, while `entry["status"]` is correctly downgraded to
   `"blocked"`. Verified `_detect_boundary` returns `PHASE`/`FEATURE` **only when
   `step_id` is the last node in the phase**. `run-phase-review` is followed by
   `compute-prediction-accuracy` (and more) in all three workflows that use it
   (`feature.yaml`, `bugfix.yaml`, `spike.yaml`) — it is never phase-last.
   Therefore `_detect_boundary` always returns `NONE` for `run-phase-review`,
   the escalation path takes the fail-soft non-boundary `upsert_step_event`,
   and the written `step_events` row records `blocked` (built from `entry` via
   `_parse_history_entry`, not the stale local). **No phase-boundary metrics
   write can fire for an escalated review.** The stale `status` local is dead
   weight on this path but causes no observable wrong behavior — not worth a
   fix task; noted here for future cleanup if the pattern recurs.

3. **test_dispatch_resume.py — confirmed.** The single failure is the exempt
   ORC-69 subprocess test; the other 39 tests pass. Not a regression.

## Parity check (rule requirement)

`TestReworkRecordNodeReopen` and `TestReworkRecordEscalation` run the real
`record()` path on a real ORC-63 nodes-shape `state.yaml` fixture and a legacy
`active:[ids]` fixture, asserting documented behavior for all three branches:
re-open on needs_work/incomplete_phase, escalate at ceiling (entry blocked +
paused + dispatch exit 2), pass unchanged. Value/shape parity verified — not
key-presence-only.

## Baseline comparison (non-blocking)

Archived `feature`-schema `review_score_avg`: 7 samples, mean 7.69. Current
overall 9 is above baseline — no warning.

## Critical Issues

None.

## Important Issues

None.

## Minor Issues

- `record.py` escalation branch passes a stale local `status` ("completed") to
  `_detect_boundary` further down. Benign today (`run-phase-review` is never
  phase-last), so no fix task. If a future workflow ever places
  `run-phase-review` last in a phase, this would need revisiting. Non-blocking.

## Verdict: PASS (overall 9/10)

All 8 ACs verified, full suite green except the 5 pre-existing exempt ORC-69
failures, no critical or important findings, all 6 tasks complete. Ready to
advance.
