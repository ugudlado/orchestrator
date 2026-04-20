# Learn Evaluation: pricing-table-in-duckdb

**Date:** 2026-04-20
**Evaluator:** workflow-improver (claude-sonnet-4-6)
**Feature:** pricing-table-in-duckdb
**Final score:** implement 9.4/10 (approved); specify 9.5/10 on re-review (approved)

---

## Execution Summary

- Specify phase: 1 rejection (7.5/10) → architect revised → 9.5/10 approved
- Implement phase: 9 developer spawns (no `repeat_until` honoring — driver manually reset `next_step` each time); 1 rejection (7.5/10) → driver fixed inline → 9.4/10 approved
- Tasks predicted: 15, actual: 15 (100% accuracy, 0% rework rate)
- One out-of-scope backlog edit by developer (T-5/T-6 spawn) reverted by driver
- One env var mismatch (`ORCHESTRATOR_DB` vs `METRICS_DB`) caught by T-15 smoke test

---

## Candidate Analysis

### A — `dispatch-repeat-until-honor` recurrence

The `execute-next-task` repeat_until bug recurred in full: driver manually reset `next_step.step_id = execute-next-task` after each of 9 developer spawns because `dispatch.py._find_completed_step` treats any completed entry as done, ignoring the predicate. Existing backlog entry `dispatch-repeat-until-honor` correctly describes this bug and scope.

**Action:** Bumped `Recurrence: 1 → 2` in `spec/changes/backlog.md`. No step-contract rule appropriate — this is a code bug, not a behavioral drift.

### B — `run-phase-review` output key validation

The driver twice submitted `review_path` instead of `phase_review_report` to `orchestrator record`, which rejected the call. Recurred once per phase (both specify and implement review).

**Action:** Not encoded. The step contract's `outputs:` section already names the expected key (`phase_review_report`). The failure point is `record.py`'s error message, which is application code outside this agent's scope. Logged here as "better fix is record.py surfacing the exact expected key in its rejection message." Not a workflow drift pattern.

### C — Architect caller-site claim, unverified (specify-phase F-1)

The architect wrote in design.md §4: "The caller in `record.main()` already holds the open DB connection; it's passed through." The specify-phase reviewer verified by grep that `record.py` has no `import duckdb` anywhere and `record.main()` opens no DB connection. The unverified claim propagated into tasks.md T-6, causing a critical finding that forced a full re-spin of all three artifacts (architect attempt 2, new review spawn — ~45 minutes and ~$4 in tokens).

This is a sibling pattern to the existing SQL-field-name-drift rule in `design-and-draft-artifacts.yaml`. Both involve the architect asserting a fact about existing code without grepping.

**Action:** Added rule to `config/steps/design-and-draft-artifacts.yaml` (cycle 16).

### D — Developer out-of-scope edit (T-5/T-6 spawn)

The T-5/T-6 developer added a `metrics-no-data-graceful` entry to `spec/changes/backlog.md`, which is not in the T-5/T-6 declared Files list. Driver discarded the unstaged edit. The existing developer contract says "keep scope focused" but does not explicitly prohibit writes to files outside the task's Files list.

**Action:** Added rule to `config/steps/execute-next-task.yaml` (cycle 16).

### E — Synthetic performance budget in design.md

The design specified "1000 calls < 50ms" (a microbenchmark). The T-5/T-6 developer found that the correct per-call SELECT was 7× over this budget in a tight loop (380ms for 1000 iterations) and pivoted to a load-all cache. The cache is architecturally correct for the use case (short-lived process), but the pivot was driven by a synthetic budget, not a real production constraint. The design lacked any statement of the absolute production target (which was trivially met — microseconds per call after caching).

**Action:** Added rule to `config/steps/design-and-draft-artifacts.yaml` (cycle 16).

### F — Shell script env var mismatch (ORCHESTRATOR_DB vs METRICS_DB)

`estimate-cost.sh` used `ORCHESTRATOR_DB` while the Python layer uses `METRICS_DB` (derived from `ORCHESTRATOR_HOME`). T-15 smoke test caught this: the script fell back to its YAML pricing path, producing different values. The test was marked failing until the driver corrected the env var name inline. This is a project-specific naming convention, not a workflow-mechanics rule.

**Action:** Added to `spec/project.yaml` learnings as `shell-script-env-vars-match-python-canonical`.

---

## Files Modified

| File | Change |
|------|--------|
| `config/steps/design-and-draft-artifacts.yaml` | Added 2 rules (C: caller-site claims, E: perf budgets) |
| `config/steps/execute-next-task.yaml` | Added 1 rule (D: out-of-scope edits) |
| `spec/project.yaml` | Added 1 learning (F: env var naming) |
| `spec/changes/backlog.md` | Bumped `dispatch-repeat-until-honor` recurrence 1→2, added source |

---

## Rules Not Encoded

| Candidate | Disposition |
|-----------|-------------|
| B (output key validation) | Not encoded — failure is in `record.py` error message clarity, not in workflow drift. Code fix is out of scope for this agent. |

---

## Backlog Entries Checked

- `dispatch-repeat-until-honor`: confirmed still present; recurrence bumped.
