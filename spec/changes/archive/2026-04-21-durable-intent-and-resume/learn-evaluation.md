# Learn Evaluation: durable-intent-and-resume

**Date:** 2026-04-21
**Evaluator:** workflow-improver (claude-sonnet-4-6)
**Feature:** durable-intent-and-resume (Phase 2 of workflow-engine-as-state-machine)
**Final scores:** specify 7.8 → 9.6 (approved after 3 inline fixes); implement 8.4 → 9.2 (approved after 1 inline AC-9 fix)

---

## Execution Summary

- Specify phase: 1 rejection (7.8/10) → driver applied 3 inline fixes (MAJOR-1 scope counts, MAJOR-2 pseudocode/prose alignment, MINOR-1 stale line number) → 9.6/10 approved
- Implement phase: 7 developer spawns across 14 tasks; 1 rejection (8.4/10) → driver applied AC-9 fixture inline → 9.2/10 approved
- Tasks predicted: 14, actual: 14 (100% accuracy, 0% rework rate)
- One developer subagent stalled at stream idle timeout (~76 min, 88 tool calls) for T-8/T-9; artifacts complete and tests green; driver committed on its behalf
- Developer self-reported test counts using broader scope (260/19 vs 192/2 baseline); informational only

---

## Candidate Analysis

### A — `_compute_attempt` latent bug caught by caller-site-verification rule

The cycle-16 caller-site-verification rule in `design-and-draft-artifacts.yaml` was the direct reason the architect independently verified that `_compute_attempt` at `dispatch.py:39-51` does not filter by status — confirming OQ-1 Option A as architecturally correct before any implementation began. The specify-phase reviewer also independently verified this claim (review-specify.md OQ-1 section). This is a confirmed hit for the rule.

Similarly, the cycle-16 absolute-perf-budgets rule produced a measurable payoff: design.md's NFR-1 cites "p99 < 5 ms end-to-end `next` invocation wall-clock" — exactly the absolute production target format the rule mandates. The specify reviewer graded NFR-1 as 10/10 (PASS).

**Action:** Bumped `hits: 0 → hits: 1` on both cycle-16 rules in `config/steps/design-and-draft-artifacts.yaml`.

### B — Developer stream-idle-timeout stall (T-8/T-9)

T-8/T-9 developer subagent stalled at ~76 minutes / 88 tool calls. Artifacts were complete and tests green at stall time; driver committed on its behalf. This mirrors the workflow-init watchdog stall from Phase 1's retro.

No new step-contract rule is appropriate: the stall is driver-side mechanism (watchdog / stream-idle detection), not subagent behavioral drift. Recording here as second recurrence of the pattern. The driver already applied the correct mitigation: verify artifacts complete → verify tests green → commit on behalf. This is good institutional practice, not something a developer prompt rule can prevent.

**Action:** None — not encodable as a workflow-mechanics rule. Pattern noted here for driver awareness.

### C — Developer test-count scope mismatch

Developer self-reported "223 passing / 2 unrelated failures" while running the narrower scope (`orchestrator_next/tests/ + scripts/tests/`). The reviewer ran the broader scope (`orchestrator_next/tests/ + scripts/tests/ + config/scripts/tests/`) and found "260 passing / 19 failures" on main, "291 / 19" on feature. Reviewer flagged as informational — no tests regressed, no quality impact, no blocking finding.

A rule here would be low-signal: the driver's inline verification already resolved the discrepancy. The developer's narrower scope was the correct scope for the feature's baseline (capture-test-baseline confirmed 192 passing / 2 failures in that scope). This is a documentation precision issue, not drift.

**Action:** None — not worth encoding. Would add prompt noise without preventing a real quality failure.

### D — AC-9: executable test substituted by prose-only SKILL.md contract

The spec's AC-9 Verify line named `test_dispatch_resume.py::test_resume_emits_stderr_log_in_auto_mode` as an executable test. The developer argued that the SKILL.md target was pseudocode, not an executable Python file, and wrote a prose-only contract instead — without escalating to the architect. The reviewer caught this as a blocking MUST-FIX (no partial credit: AC Verify is a hard spec commitment).

The fix was trivial: a 43-line subprocess fixture + 3 tests, matching the exact pattern the reviewer suggested in the fix direction. This pattern is not covered by any existing rule:
- `developer-scope-files-only` addresses extra edits, not missing tests
- TDD rule is generic and doesn't address the "feasibility escape hatch → escalate, don't substitute" path
- No existing rule addresses the case where spec names a specific test that the developer judges impractical without architect sign-off

The root cause is that developers face a choice when a spec-named test seems infeasible: (a) implement anyway via a fixture/subprocess, (b) escalate to architect. Choosing option (c) — silent substitution — is not a valid path, but the current contracts don't make this explicit.

**Action:** Added rule to `config/steps/execute-next-task.yaml` (cycle 17).

### E — Reconcile seed-both-sides correctness (T-13/T-14)

The T-13/T-14 developer correctly identified that seeding only state.yaml with an in_progress entry doesn't survive reconcile (FR-4: DB wins on reconcile), so tests must seed both DB and YAML. This was caught at implementation time, not test-writing time, and was handled correctly without a retro-worthy failure.

**Action:** None — architectural understanding is working. No rule needed.

### F — Test method names drift from spec AC Verify citations

The implement-phase reviewer noted (as SUGGESTION, non-blocking) that AC-1, AC-3, AC-6, AC-7, AC-8, and AC-10 cite specific test method names that don't exist. The developer split several tests and used different naming. Functional coverage was correct in every case.

Encoding this as a rule would mandate either (a) spec-draft-time method name commitment (premature) or (b) developer rename-to-match-spec (unnecessary overhead when coverage is correct). The reviewer marked it non-blocking and did not require a fix before approval.

**Action:** None — would generate spec-hygiene busywork without preventing quality failures.

---

## dispatch-repeat-until-honor Check

The feature did not trigger the `dispatch-repeat-until-honor` bug. The discoverer's key_finding_1 explicitly confirmed "Phase 2 does NOT subsume dispatch-repeat-until-honor — different layers." State.yaml shows no manual `next_step` resets required across 7 developer spawns. Backlog entry stays at **Recurrence: 2**.

---

## Files Modified

| File | Change |
|------|--------|
| `config/steps/design-and-draft-artifacts.yaml` | Bumped `hits: 0 → 1` on caller-site-verification rule (line 30) and absolute-perf-budgets rule (line 31) |
| `config/steps/execute-next-task.yaml` | Added 1 rule: executable test required when spec AC Verify names a specific method (cycle 17) |

---

## Rules Not Encoded

| Candidate | Disposition |
|-----------|-------------|
| B (stream idle stall) | Not encodable — driver-side watchdog mechanism, not agent drift |
| C (test-count scope) | Not encoded — informational finding, driver inline fix sufficient |
| E (seed-both-sides) | Not encoded — positive signal; correctness held without a rule |
| F (test method name drift) | Not encoded — non-blocking suggestion; spec-hygiene overhead outweighs gain |

---

## Backlog Entries Checked

- `dispatch-repeat-until-honor`: NOT hit this feature run; recurrence stays at 2.

---

## Cycle-16 Rule Validation

| Rule | Hit this feature? | Evidence |
|------|-------------------|----------|
| caller-site-verification | YES — `_compute_attempt` latency bug caught at design time | review-specify.md OQ-1 section; architect evidence block in state.yaml |
| absolute-perf-budgets | YES — NFR-1 cites "p99 < 5ms end-to-end wall-clock" (absolute target format) | review-specify.md NFR-1 PASS |
| developer-scope-files-only | CLEAN — no out-of-scope edits attempted | state.yaml step_history; no driver reverts recorded |
| shell-script-env-vars-match-python-canonical | N/A — no shell scripts in this feature's scope | N/A |
