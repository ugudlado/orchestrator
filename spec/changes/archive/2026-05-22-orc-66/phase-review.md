# Phase Review: ORC-66 implement phase

Feature: One developer spawn per task + `max_parallel` + pure-orchestration driver + step classification
Schema: feature · Phase: main (implement) · Reviewer: independent verification
Date: 2026-05-22

## Scoring config (project.yaml `quality_bar.scoring`)

critical_cap 5 · important_cap 7 · green_base 9 · min_phase_review_score 9 · max_retry_rounds 8

## Dimension Scores

| Dimension | Score | Key Notes |
|-----------|-------|-----------|
| Spec Compliance | 10 | All 7 ACs verified with evidence. Audit exhaustive (30/30 contracts). |
| Correctness | 10 | 534/534 tests pass. Contract rewrites internally consistent. No regressions. |
| Security | 10 | No new attack surface — config/prose/parser changes only. No secrets, no eval, no injection path. |
| Simplicity | 10 | Approach 3 (lowest-complexity valid option) implemented in full. No per-task node graph, no new contracts, no subdag.py. |
| Code Quality | 10 | `tasks_ready.py` is a clean pure parser; tests substantive (positive + negative controls). Follows existing fixture patterns. |
| **Overall** | **10** | min of dimensions (9) + first-pass bonus (+1): no retries this round, no TODO/FIXME, no failed verify assertions. |

## Verification (re-run independently in worktree)

- **Type-check:** N/A — repo has no type-checker (`verify_commands` declares only `test`).
- **Tests:** `python3 -m pytest config/scripts/orchestrator_next/tests/ -q` → **534 passed**, 28 warnings, 8.28s. Matches developer claim exactly.
  - Baseline at `capture-test-baseline`: 487 passing / 3 failing / 490 total. Current: 534 passing / 0 failing / 534 total. +47 tests, the 3 prior failures resolved — no regression, net improvement.
  - ORC-66-specific modules (24 tests): `test_execute_next_task_per_task.py`, `test_max_parallel_flag.py`, `test_driver_per_task_dispatch.py`, `test_driver_pure_orchestration.py`, `test_step_classification_rule.py`, `test_step_classification_docs.py` — all pass.
  - repeat_until / redispatch coverage: 8 tests pass.
- **Build:** N/A — no build step in this repo.
- **Schema verify block:** `feature.yaml` carries no `verify:` block (only a `steps:` list) — no schema commands/assertions/metrics to run. `min_phase_review_score` (9) sourced from `project.yaml`.
- **Git:** 19 commits on `feature/orc-66`, one per task plus a cleanup commit. Matches developer claim.
- **Phase completeness:** all 18 tasks `[x]` in tasks.md. No unchecked items. No `quarantine_events` in state.yaml. All step_history entries `attempt: 1` — zero retries across the whole workflow.

## Acceptance Criteria — verified with evidence

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| AC-1 | Step Classification Audit classifies every `config/steps/*.yaml` contract | PASS | Programmatic count: audit names 30 distinct ids = 30 contracts on disk. Sub-sections: 19 run + 9 agent + 1 pre-init (`select-workflow`) + 1 examined (`execute-next-task`). Zero missing, zero extra. `test_step_classification_docs.py` asserts set equality. |
| AC-2 | `execute-next-task` dispatches one developer agent per task, `depends:`-ordered; agent owns implement/verify/commit/`[x]` | PASS | `execute-next-task.yaml` v5: instruction scopes a spawn to ONE assigned `task_id`; no "complete all tasks"/queue-drain language (grep clean — only match is a retry-cap phrase, semantically correct). `repeat_until: all_tasks_completed` retained (line 11). `flags_read: max_parallel` declared. `tasks_ready.ready_task_ids` excludes tasks with unsatisfied `depends:`. `test_execute_next_task_per_task.py` + `test_driver_per_task_dispatch.py` pass. |
| AC-3 | `max_parallel` behavioral flag (default 1, integer), `--max-parallel` CLI binding, `min(max_parallel, ready)` spawn count | PASS | `flags.yaml` `behavioral.max_parallel: { default: 1, description: ... }`; `cli.--max-parallel` sets `max_parallel`. `seed-state.sh` `_coerce` resolves int before string (bool→int→str order) — `test_max_parallel_flag.py` proves `max_parallel=3` lands as integer 3 in `state.yaml.flags`, default resolves to int 1, boolean flags unaffected. SKILL.md per-task block: `spawn_count = min(max_parallel, len(ready_set))`. |
| AC-4 | Driver pure orchestration — no ticket/state side effects in the dispatch loop | PASS | SKILL.md dispatch loop + per-task block: `next`/`ready` → spawn → `done` → loop; explicitly "no ticket edits, no git commit, no state.yaml mutation." `test_driver_pure_orchestration.py` greps the loop and developer/reviewer/linear skills — no `backlog task edit --check-ac|--notes|--final-summary`, no `git commit`, no state.yaml mutation. Audit found loop already clean (T-12 no-op confirmation); the test is the permanent guard. |
| AC-5 | `step-classification` named rule in project.yaml propagates to agent step nodes via rule-merge | PASS | `project.yaml rules:` carries `id: step-classification`, no `when:`, litmus-test text. `test_step_classification_rule.py`: positive (`generate_plan` over feature schema → litmus text in an agent node's merged `rules`), negative control (no rule → absent), flag-independence, and a real-`project.yaml` assertion — all pass. |
| AC-6 | `## Step Classification` section in CONVENTIONS.md between SRP and Structure | PASS | `CONVENTIONS.md`: `## Step Classification` at line 55, between `## Single Responsibility Principle` (47) and `## Structure` (87). Body carries the litmus-test sentence, burden-of-proof-on-`agent:` rule, and the unit-of-work split rule with the agent-owns-its-commit clarification. `test_step_classification_docs.py` asserts placement + litmus substring. |
| AC-7 | Workflow resumed mid-implementation: `repeat_until` re-dispatches `execute-next-task` until `tasks.md` has zero `- [ ]` | PASS | `repeat_until: all_tasks_completed` retained in `execute-next-task.yaml` (line 11) — the re-dispatch mechanism (`readiness.repeat_until_redispatch`, predicate = zero `- [ ]` in tasks.md) is reused unchanged. 8 repeat_until/redispatch tests in the full suite pass. `tasks.md [x]` markers are the durable resume signal. |

## Use Case Traceability

| Use Case | ACs | Status |
|----------|-----|--------|
| UC-1 | AC-2, AC-7 | Covered |
| UC-2 | AC-2, AC-3, AC-7 | Covered |
| UC-3 | AC-4, AC-5, AC-6 | Covered |
| UC-E1 | AC-2 | Covered |
| UC-E2 | AC-3 | Covered |
| UC-E3 | AC-1, AC-6 | Covered |

## Self-modification check

ORC-66 rewrites the engine's own contracts (`execute-next-task.yaml`, `developer.md`, `developer/SKILL.md`, `orchestrate/SKILL.md`, `flags.yaml`, `project.yaml`, `CONVENTIONS.md`). All edits are in the worktree; the live engine running this review is unaffected. The rewritten contracts are internally consistent:
- `execute-next-task.yaml` v5 says "one task per spawn" throughout — instruction, rules, verify block, closing prose — with no residual queue-drain language.
- `agents/developer.md` and `skills/developer/SKILL.md` describe one-task scope only; "do not drain the queue" stated explicitly. No contradiction.
- `repeat_until: all_tasks_completed` deliberately retained — the per-task loop is the same redispatch primitive, only the instruction changed. Correct.

## Baseline comparison (non-blocking)

Feature archives with `metrics.review_score_avg`: 9.0, 8.3, 9.0, 0, 9.0, 9.0, 9.5 → average **7.69**. The 0 belongs to `single-source-metrics-via-step-events` (an unrecorded score, not a true 0) which depresses the mean. Current overall 10 is well above baseline — no 2-below warning.

## Findings

### Critical
None.

### Important
None.

### Minor / Suggestions (non-blocking — no fix tasks generated)

1. **[SUGGESTION]** `flags.yaml` `--max-parallel: { sets: { max_parallel: <N> } }` uses a literal `<N>` placeholder. This is consistent with how the orchestrate skill translates CLI args into `key=value` seed-state overrides, and `test_max_parallel_flag.py` exercises the `max_parallel=3` seed-state path directly. There is no test for the literal `--max-parallel 3` → `max_parallel=3` arg-parse step itself (it lives in skill prose, not a script). AC-3's wording ("a `--max-parallel` `cli:` binding sets `max_parallel`") is satisfied by registration + the verified integer seed-state path. Worth a future test if the CLI-arg translation ever becomes a script.

## Verdict: PASS

Overall 10/10 ≥ `min_phase_review_score` 9. Zero critical findings, zero important findings. All 7 ACs verified with evidence. All 18 tasks complete. 534/534 tests pass with no regression. First-pass bonus awarded: no retries this round, no TODO/FIXME/placeholder in changed files, no failed verify assertions.
