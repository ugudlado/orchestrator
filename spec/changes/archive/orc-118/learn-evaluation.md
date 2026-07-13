# Learn Evaluation: orc-118

**Date:** 2026-07-13
**Evaluator:** workflow-improver (claude-sonnet-4-6)
**Feature:** orc-118 — user-level model configuration (~/.orchestrator/models.yaml)
**Final scores:** design 9/10 (first-pass approved); implement 10/10 (first-pass approved, first-pass bonus applied)

---

## Execution Summary

- Design phase: 0 rejections; 9/10 first-pass approved
- Implement phase: 6 tasks, 1 implement-tasks spawn, all 6 completed with per-task verify green; 10/10 phase review on first pass
- Tasks predicted: 6, actual: 6 (100% accuracy, 0% rework rate)
- **Routing regression** (external engine bug): run-phase-review PASS verdict was incorrectly reset to pending instead of advancing to ticket-qa. This caused implement-tasks to re-run 8 times (7 wasted dispatches). Root cause: commit 2ff6ade introduced on_success/on_failure conflation in _apply_routing. Fixed in commit 6ad40cf before workflow continued.
- SKIP=pytest used at commit time (pre-commit hook blocked by GIT_DIR in nested-git test environment); existing `git-dir-pollution-pre-commit-hook` rule correctly predicted this pattern — confirmed HIT.
- xfail(strict=False) rule followed correctly: T-1 RED and T-3 RED tasks both marked tests with xfail as prescribed — 0 TDD contract contradictions; confirmed HIT for xfail rule.
- verify-scope rule followed correctly: all 6 task verify commands were satisfied by files in each task's scope — confirmed HIT.

---

## Candidate Analysis

### A — on_success routing regression (engine bug, commit 2ff6ade → 6ad40cf)

The routing regression caused 7 wasted implement-tasks dispatches. Root cause: _apply_routing in record.py treated all named step_id routing targets as loop-backs (reset both gate and target), without distinguishing on_success (advance forward) from on_failure (reset for re-verification). Fixed in 6ad40cf by adding a status-aware elif branch.

This is a code bug, not a behavioral drift in any step contract. No step-contract rule can prevent an engine routing bug. Logged here as second known instance of engine routing conflation (first was the original loop-back fix in 2ff6ade).

**Action:** None — code fix applied in 6ad40cf. Regression test added (test_phase_review_success_routing.py). Not encodable as a workflow rule.

### B — xfail(strict=False) rule: confirmed HIT

ORC-118 TDD tasks (T-1 RED, T-3 RED) correctly used `@pytest.mark.xfail(strict=False)` as prescribed by the rule in design-and-draft-artifacts/prompt.md. The developer completed all 6 tasks with 0 TDD contract contradictions (prior ORC-118 runs without this rule saw both abandons). Rule is working.

**Action:** Bump `hits: 3 → hits: 4` on the xfail rule in `config/steps/design-and-draft-artifacts/prompt.md`.

### C — verify-scope rule: confirmed HIT

All 6 task verify commands were satisfied by the files in each task's declared scope. No verify command imported or called an out-of-scope file. Prior runs (before this rule was encoded) saw T-2 abandon due to a verify importing an unlisted file.

**Action:** Bump `hits: 3 → hits: 4` on the verify-scope rule in `config/steps/design-and-draft-artifacts/prompt.md`.

### D — git-dir-pollution-pre-commit-hook rule: confirmed HIT

implement-tasks used SKIP=pytest for commits (as allowed by the existing rule in spec/project.yaml), because the pre-commit hook cannot run when GIT_DIR is set and tests invoke nested git operations. The task-scoped verify commands were confirmed green before each commit. Correctly flagged in known_concerns.

**Action:** None — existing rule covers this. No further encoding needed.

### E — Phase-gate scope rule: confirmed HIT

Design scoped the phase-gate task (T-6) to feature-specific test files only (`pytest tests/test_model_routes.py tests/test_models_verb.py tests/test_doctor_model_sources.py`) rather than the full suite, correctly anticipating pre-existing baseline failures. Phase-gate passed without blocking.

**Action:** Bump `hits: 2 → hits: 3` on the phase-gate-scope rule in `config/steps/design-and-draft-artifacts/prompt.md`.

---

## Files Modified

| File | Change |
|------|--------|
| `config/steps/design-and-draft-artifacts/prompt.md` | Bump hits: 3→4 on xfail rule (B); hits: 3→4 on verify-scope rule (C); hits: 2→3 on phase-gate-scope rule (E) |

Note: sandbox write restrictions prevent direct edits to step contracts from the worktree session. Hit counter updates above should be applied at merge time or by the run-learn-cycle in main-repo context.

---

## Rules Not Encoded

| Candidate | Disposition |
|-----------|-------------|
| A (routing regression) | Not encoded — engine code bug, fixed in 6ad40cf with regression tests. Not workflow drift. |

---

## Quality Bar Assessment

Phase review scores: design 9/10, implement 10/10. Quality bar (min: 9) met on both phases. No adjustment needed.

## Cycle Metrics

- Rules confirmed (HIT): 4
- Rules added: 0
- Rules updated (hit count): 3 pending at merge
- Cost: design $3.07, implement $0.65, review $0.78; total ~$4.50
- Routing bug wasted dispatches: 7 (not chargeable to feature quality)
