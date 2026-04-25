# Retro: cleanup-and-delete (Phase 5)

## What shipped

- Absorbed `scripts/inline/ingest-feature-metrics.py` (440 lines) into `record.py`'s complete-phase write path. Trigger fires at `step_id == "mark-change-completed"` AND `status == "completed"`, NOT at the generic feature boundary — because `compute-swe-metrics` reads `feature_metrics` via LEFT JOIN at position 5, before `remove-worktree` (position 7).
- New helpers in `record.py`: `_resolve_feature_metrics` (parses outside transaction) + `_write_feature_metrics` (inserts inside). 6 computation functions (`parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`) moved verbatim.
- Atomic transaction: BEGIN → step_events upsert → _write_feature_metrics → COMMIT, fatal ROLLBACK on failure (consistent with Phase 4's OQ-2).
- Shape-parity test (`test_feature_metrics_parity.py`) using a captured JSON snapshot from the `done-verb-level-aware-writes` archive fixture (24 columns excluding `source` + `computed_at`).
- Stage B deletions: `scripts/inline/ingest-feature-metrics.py`, `config/steps/ingest-feature-metrics.yaml`, `config/scripts/__tests__/test-ingest-feature-metrics.sh`, the entry in `config/workflows/_complete-phase.yaml`, and updates to `config/tests/test-complete-phase-order.sh` + `verify-all.sh` + baseline fixtures.
- 15 commits (13 task commits + 1 driver Gate-4 fix + 1 driver FT-20 fix). 53 new tests (342 passing, 2 pre-existing failures unchanged).

## What scored

- Specify phase: 9/9 round 1 (no critical/important/minor findings — cleanest specify in the parent refactor's history).
- Implement phase: 9/9 round 2 (round 1 caught CF-1 — see below). 9/9 ACs verified with evidence.

## Critical bug discovered AND fixed inside this feature: FT-20

### Symptom
After Stage B deleted `ingest-feature-metrics.yaml`, `dispatch.py:353` in the `run_step` branch would raise `FileNotFoundError` when the frozen `workflow_plan.complete.active` list still referenced the deleted step. This would crash any in-flight workflow's complete phase before `compute-swe-metrics`/`archive-completed-change`/`remove-worktree` could run — including this very feature's own complete phase.

### Why it shipped
The architect's spec correctly flagged the self-bootstrapping hazard for `_complete-phase.yaml` (Stage B keeps the inline script alive through the workflow's own complete phase via fail-soft skipping in record). But it missed the `dispatch.py` side: even when the inline script is fail-soft, the dispatcher itself reads the contract YAML BEFORE the script runs. The resume_step branch at `dispatch.py:282-289` already had the try/except fallback — the run_step branch did not. The asymmetry was invisible until reviewer round 1.

### Fix
5 lines: wrap `dispatch.py:353-365` in try/except `FileNotFoundError`, fall back to `StepContract(id=next_step_id, agent="inline", run=None, instruction="", rules=[])`. Mirrors the existing pattern. Plus 1 regression test (`test_dispatch_missing_contract.py`) asserting no FileNotFoundError raises and the stub action returns.

### Lesson (cycle-23 candidate rule)
**When a phase deletes step contracts referenced by a frozen `workflow_plan`, the dispatcher's `run_step` branch must mirror the `resume_step` branch's try/except `FileNotFoundError` fallback.** Already-frozen plans are an inherent property of `workflow-plan-upfront`; the architect/reviewer must check both code paths, not just one. Phase 4 escaped this because nothing in its complete phase pointed at deleted contracts at dispatch time.

## Other discoveries

- **Gate 4 count drift after deletions** (already a learned-rule candidate from Phase 4): T-14 deleted `ingest-feature-metrics.py` but didn't update `m8-gates.sh` Gate 4's count assertion (10 → 9). Driver fixed in commit `f6b0caf`. Same pattern as Phase 4's Gate 4 update during T-26. Worth elevating to a rule: "When deleting files under `scripts/inline/`, grep `m8-gates.sh` for hardcoded counts and update atomically."
- **TDD audit-trail gaps**: developer batched T-3/T-5 tests into the T-1 commit, and T-9 never went RED because Stage A finished before the parity-test task ran. Logic correct, audit trail thin. Reviewer flagged as minor. Pattern is consistent with batched-developer-spawn approach (Phase 4 same).
- **`task_checkpoint` per-task not written**: developer batched 19 task completions into one `execute-next-task` step_history entry. Auditability impact only.

## What worked

- **Approach A (mirror Phase 4)** was clearly correct from the start — the architect picked it without iteration. Phase 4's pattern paid forward.
- **Snapshot-swap parity test pattern** (T-15): clean way to handle "test parity against legacy script, then legacy script is deleted" — capture expected-row JSON during T-10, swap test to compare against snapshot in T-15.
- **`flags.agents: true`**: cleaner than Phase 4's `false`. Developer chain ran one big spawn covering all 19 tasks; no manual chaining by the driver.
- **Trigger-point discovery in specify**: the discoverer caught the `compute-swe-metrics` LEFT JOIN constraint that made "feature boundary = remove-worktree" wrong. Without that, the absorption would have written the row too late and broken `compute-swe-metrics` silently. This is the kind of architectural detail discovery is supposed to surface — and did.

## What didn't work / friction points

- The `dispatch.py` run_step vs resume_step asymmetry was a blind spot. The architect read both branches but didn't notice the missing try/except in run_step. Adding the FT-20-style asymmetry check to design review checklists would help.
- Same `_check_all_tasks_completed` `.state/<slug>/tasks.md` lookup bug from Phase 4 retro is still unfixed — the dispatcher fail-opened to "all tasks done" again. Workaround was the same (driver records per-spawn, not per-task).
- Same orphan `in_progress` row pattern from Phase 4 retro hit again at design-and-draft-artifacts. Same workaround.
- Linear MCP still unauthenticated — ticket creation deferred for the second consecutive feature.

## Numbers

- Specify phase: 1 review round (9/9), 0 findings.
- Implement phase: 2 review rounds (5 → 9), 1 critical (FT-20) caught and fixed in round 1.
- Test count: 288 → 342 (+54 net, including 1 regression test for FT-20).
- Lines net: -440 (`ingest-feature-metrics.py` deletion) + ~280 added (helpers + computation functions + tests + dispatcher fallback) = ~-160 net.
- Commits: 15 on feature branch.

## Status of parent refactor: workflow-engine-as-state-machine

All 5 phases shipped:
- ✅ Phase 1: pricing-table-in-duckdb (Apr 21)
- ✅ Phase 2: durable-intent-and-resume (Apr 21)
- ✅ Phase 3: report-views-retire-cli (Apr 24)
- ✅ Phase 4: done-verb-level-aware-writes (Apr 25)
- ✅ Phase 5: cleanup-and-delete (Apr 25)

Parent refactor complete. Net result vs. original goal: orchestrator CLI surface narrowed to two real verbs (`next`, `done`) plus `record` (silent compat alias) and `doctor` (maintenance). Reports are SQL views. Writes are level-aware and atomic. Durable intent and idempotent resume work. ~700 lines of Python projection + ingest code retired. Salvage path (`status: recovered`) lives in `done` for future use.
