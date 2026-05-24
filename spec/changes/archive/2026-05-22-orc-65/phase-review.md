## Phase Review: Task-DAG expansion — flat task-nodes via expand-plan (ORC-65)

**Reviewer:** Reviewer agent (run-phase-review, attempt 2)
**Date:** 2026-05-23
**Phase:** main (implement)
**Tasks completed:** 19/19 (T-1..T-17 + T-18 + T-19)

---

## Verification Gates

| Check | Result | Detail |
|-------|--------|--------|
| Unit tests | PASS | 536 passed, 0 failed (8.58s) |
| Integration: test-expand-plan.sh | PASS | 13/13 passed |
| Integration: test-flat-task-nodes-e2e.sh | PASS | 10/10 passed |
| T-18: stale execute-next-task refs in agents/ | PASS | `grep -rn execute-next-task agents/` returns 0 matches |
| T-19: stale test file deleted | PASS | `test-execute-next-task-simplify-pass.sh` does not exist |
| Type-check | N/A | Python project — no static type-check step configured |
| Build | N/A | Library, not compiled |

---

## Score

| Dimension | Score | Notes |
|-----------|-------|-------|
| Spec Compliance | 9/10 | All 17 ACs pass; T-18/T-19 closed the AC-10 gaps from attempt 1 |
| Correctness | 9/10 | expand-plan idempotent, atomic write, cycle/unknown-id detection, topo-sort reused |
| Security | 10/10 | No user input in eval paths, no new injection surfaces |
| Simplicity | 9/10 | Flat-node model is the simplest design, well-executed; legacy fallback in record.py intentional |
| Code Quality | 9/10 | Consistent with project patterns; stale references resolved |
| **Overall** | **9/10** | No critical issues remaining |

---

## AC Verification

### Stage 1 — Architect emits tasks.yaml

**AC-1**: `design-and-draft-artifacts.yaml` step 7 includes full `tasks.yaml` generation
instructions, references the Tasks YAML Format Contract, and lists `tasks.yaml` in
`outputs:`. Unit test `test_design_and_draft_emits_tasks_yaml.py` — 4/4 passed. **PASS**

**AC-2**: `config/scripts/inline/validate-tasks-yaml.sh` exists. Unit tests cover
well-formed, duplicate ids, unknown depends_on, missing fields — 11/11 passed.
`artifact-formats.md` has "Tasks YAML Format Contract" section. **PASS**

### Stage 2 — expand-plan CLI verb

**AC-3**: `expand_plan.expand_plan()` appends flat task-nodes per task in `tasks.yaml`.
Integration test confirms task-T-1, task-T-2, task-T-3 appear with correct
`depends_on` and `task:` payloads. CLI exits 0 on success. **PASS**

**AC-4**: Second invocation is byte-identical (confirmed by integration test sha256 check). **PASS**

**AC-5**: Cycle in `tasks.yaml` causes non-zero exit; `state.yaml` unchanged. **PASS**

**AC-6**: Unknown `depends_on` id causes non-zero exit; `state.yaml` unchanged. **PASS**

**AC-7**: After `expand-plan`, `run-phase-review.depends_on == ["task-T-3"]` for a 3-task plan.
Integration test confirmed. **PASS**

### Stage 3 — Wire expand-plan, replace execute-next-task

**AC-8**: `config/workflows/feature.yaml`, `bugfix.yaml`, `spike.yaml` all list `expand-plan`.
`grep execute-next-task` across config/workflows/ returns zero matches. **PASS**

**AC-9**: `execute-one-task.yaml` is 59 lines (< 80). `grep -c repeat_until` = 0. Multiple
`step_context.task` references confirmed. **PASS**

**AC-10**: `execute-next-task.yaml` deleted. `_check_all_tasks_completed` removed from
`record.py`. `REPEAT_PREDICATES` entry removed from `readiness.py`.
- Runtime files clean: `grep execute-next-task agents/` = 0 matches (T-18 fix).
- Stale test file deleted: `test-execute-next-task-simplify-pass.sh` absent (T-19 fix).
- `config/grammar.yaml` has removal comment — intentional documentation.
- `config/steps/CONVENTIONS.md` has removal-marker table row — intentional documentation.
- Unit tests referencing `all_tasks_completed` document the removal — correct.
**PASS**

**AC-11**: Three discrete `step_history` entries for task-T-1/T-2/T-3 with status=completed
confirmed by `test-flat-task-nodes-e2e.sh` Test A. **PASS**

**AC-12**: Resume after task-T-1 completed returns `step_id == task-T-2` confirmed by
`test-flat-task-nodes-e2e.sh` Test C. **PASS**

**AC-13**: `run-phase-review.yaml` `needs_work` branch appends fix tasks to `tasks.yaml`,
calls `orchestrator expand-plan $STATE_YAML`, returns COMPLETION with `needs_work`.
`grep execute-next-task config/scripts/orchestrator_next/record.py` = 0 matches. **PASS**

**AC-14**: `test-flat-task-nodes-e2e.sh` Test B confirms `orchestrator graph` output contains
`task-T-1`, `task-T-2`, `task-T-3` Mermaid identifiers. **PASS**

### Stage 4 — Per-task telemetry

**AC-15**: `upsert.py` confirms `step_events` primary key includes `step_id` and uses
`entry.step_id` directly. Since task-nodes use `id: task-T-N` and `record.py` stores
that as `step_id` in every `step_history` entry, DuckDB rows land as `task-T-1`,
`task-T-2`, `task-T-3` without code change. Design.md § Components documents this path. **PASS**

**AC-16**: `compute_task_counts()` in `record.py` reads `step_history` entries whose
`step_id.startswith("task-")`, counting `status in (completed, recovered)`.
Unit test `test_t13_compute_resolution_from_step_history.py` — 6/6 passed. **PASS**

### Stage 5 — Delete tasks.md

**AC-17**: `design-and-draft-artifacts.yaml` no longer writes `tasks.md`.
`artifact-formats.md` "Task Format Contract" section removed.
`grep tasks.md config/steps/` = 0 matches.
`record.py` retains `parse_tasks()` as a deprecated legacy shim (explicitly chosen in T-13). **PASS**

---

## Critical Issues

None.

---

## Important Issues (should fix in follow-on)

- `skills/orchestrate/SKILL.md:115` says "pass full tasks.md queue" — now outdated.
  Should reference `tasks.yaml` or the `step_context.task` mechanism in a follow-on cleanup.
  Not blocking because the functional path (skills/developer/SKILL.md) is correct.

---

## Minor Issues (nice to have)

- `record.py:parse_tasks()` deprecation comment is adequate for one-cycle retention; should
  be deleted in the next cleanup cycle.

---

## Verdict: PASS

Score 9/10. All 17 ACs verified. Both critical issues from attempt 1 resolved:
1. T-18: stale `execute-next-task.yaml` references removed from all agent definition files.
2. T-19: `test-execute-next-task-simplify-pass.sh` deleted; test suite is clean.

536 unit tests pass, all integration tests pass, no critical findings remain.
