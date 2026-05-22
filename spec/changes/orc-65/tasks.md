# Tasks — Task-DAG expansion (flat task-nodes via expand-plan)

## Stage 1 — Architect emits tasks.yaml (additive, zero behavior change)

- [x] T-1: Define tasks.yaml schema and add validator script
  Why: AC-1, AC-2 — establish the machine-readable task contract before any consumer uses it
  Files: config/steps/contracts/artifact-formats.md, config/scripts/inline/validate-tasks-yaml.sh
  Change: append a "Tasks YAML Format Contract" section to artifact-formats.md (mirror the schema in design.md § State Management); create validate-tasks-yaml.sh as a thin Python wrapper that loads the file, checks required fields, duplicate ids, and unknown depends_on (cycle check deferred to expand-plan)
  Test scenarios:
    - validator exits 0 on a well-formed file
    - validator exits non-zero on duplicate ids
    - validator exits non-zero on unknown depends_on
    - validator exits non-zero on missing required field

- [x] T-2: Architect step writes tasks.yaml alongside tasks.md
  Why: AC-1 — produce the new artifact in every feature/bugfix run
  Files: config/steps/design-and-draft-artifacts.yaml
  Change: extend the instruction block (step 7) to also write tasks.yaml from the same task plan; add tasks.yaml to outputs and to the verify after_each checklist; reference artifact-formats.md § Tasks YAML Format Contract
  Test scenarios:
    - architect step completion lists both tasks.md and tasks.yaml in outputs
    - validate-tasks-yaml.sh passes on the architect's output (smoke test via run-phase-review)
  depends: T-1

## Stage 2 — expand-plan CLI verb (unwired)

- [x] T-3: Implement orchestrator_next.expand_plan module
  Why: AC-3, AC-4, AC-5, AC-6, AC-7 — the core CLI verb
  Files: config/scripts/orchestrator_next/expand_plan.py
  Change: new module exposing expand_plan(state_yaml_path: str) -> None; reads tasks.yaml from worktree_artifact_dir, builds one task-node per task (id=`task-<task_id>`, agent: developer, step_contract: execute-one-task, depends_on mapped through `task-` prefix, task: payload), appends only ids not already present, calls generate_plan._topo_sort to validate the full plan, rewires run-phase-review.depends_on to the last task-node id; atomic write via pre-write byte buffer (mirror dispatch._persist_node_status)
  Test scenarios:
    - first invocation appends N task-nodes for an N-task tasks.yaml
    - second invocation appends nothing (idempotent)
    - cycle in tasks.yaml raises; state.yaml unchanged on disk
    - unknown depends_on id raises; state.yaml unchanged
    - missing required field raises with field name and task id
    - run-phase-review.depends_on becomes [last_task_node_id]
  depends: T-1

- [x] T-4: Wire expand-plan as a CLI subcommand
  Why: AC-3 — make the verb invokable
  Files: config/scripts/orchestrator_next/__main__.py (or wherever next/done/graph dispatch is registered)
  Change: add `expand-plan` subcommand wired to expand_plan.expand_plan; mirror the argparse shape used by `next` and `done`
  Test scenarios:
    - `orchestrator expand-plan <state.yaml>` exits 0 on success
    - `orchestrator expand-plan` (no arg) exits non-zero with usage message
    - `orchestrator expand-plan /nonexistent` exits non-zero with file-not-found
  depends: T-3

- [x] T-5: Integration test for expand-plan end-to-end
  Why: AC-3, AC-4, AC-5, AC-6, AC-7 — lock the verb behavior
  Files: config/tests/test-expand-plan.sh
  Change: shell test that seeds a state.yaml with the implement phase containing only [design-and-draft-artifacts, expand-plan, run-phase-review], places a tasks.yaml with 3 tasks (T-1, T-2 depends T-1, T-3 depends T-2), invokes expand-plan, asserts the plan now has task-T-1/task-T-2/task-T-3 with correct depends_on chain, asserts run-phase-review.depends_on == [task-T-3], reruns expand-plan and asserts state.yaml is byte-identical
  Test scenarios:
    - happy path appends 3 nodes with correct edges
    - idempotent rerun is a no-op
    - cycle file leaves state.yaml unchanged (compare sha256)
    - unknown-id file leaves state.yaml unchanged
  depends: T-3, T-4

## Stage 3 — Wire expand-plan, replace execute-next-task

- [x] T-6: Add execute-one-task step contract
  Why: AC-9 — the single-task developer contract
  Files: config/steps/execute-one-task.yaml
  Change: new contract, agent: developer, no repeat_until, ~40 lines; instruction tells the agent to read step_context.task (id, title, files, verify, test_scenarios), implement that one task, run verify commands, commit per auto-commit.md, return one COMPLETION; no scheduling logic, no tasks.md scanning, no loop
  Test scenarios:
    - file is < 80 lines
    - no occurrence of repeat_until
    - instruction references step_context.task
    - inputs: includes no tasks.md reference
  depends: T-3

- [x] T-7: Wire expand-plan into workflow schemas
  Why: AC-8 — the verb runs in production workflows
  Files: config/workflows/feature.yaml, config/workflows/bugfix.yaml, config/workflows/spike.yaml
  Change: replace `execute-next-task` with `expand-plan` in each schema's steps list immediately after design-and-draft-artifacts (bugfix has design-and-draft-artifacts after diagnose; spike has it after explore)
  Test scenarios:
    - grep -E '^\s*-\s*expand-plan' returns one match per schema
    - grep -E '^\s*-\s*execute-next-task' returns zero matches across config/workflows/
  depends: T-3, T-6

- [x] T-8: Replace needs_work in-place reset with expand-plan injection
  Why: AC-13 — unify task injection under expand-plan
  Files: config/steps/run-phase-review.yaml, config/scripts/orchestrator_next/record.py
  Change: in run-phase-review.yaml, change the needs_work instruction to (a) append fix-N entries to tasks.yaml with depends_on the previous final task-node, (b) invoke `orchestrator expand-plan $STATE_YAML` as a subprocess, (c) return COMPLETION with verdict needs_work; in record.py, remove the `readiness.mark_node_status(state_raw, phase, "execute-next-task", "in_progress")` call at line 1411; keep the `run-phase-review` self-reset to pending so the dispatcher schedules it again after the fix tasks
  Test scenarios:
    - grep execute-next-task config/scripts/orchestrator_next/record.py returns zero
    - needs_work COMPLETION test: tasks.yaml gains fix-N entries, state.yaml plan gains task-fix-N nodes, run-phase-review.status == pending
    - retry action (run-phase-review's other branch) still resets only itself
  depends: T-3, T-7

- [x] T-9: Delete execute-next-task contract and all_tasks_completed plumbing
  Why: AC-10 — remove the dead path so the codebase has a single story
  Files: config/steps/execute-next-task.yaml, config/scripts/orchestrator_next/record.py, config/scripts/orchestrator_next/readiness.py (only the all_tasks_completed-specific callers, not the per-node repeat_until machinery)
  Change: delete config/steps/execute-next-task.yaml; remove `_check_all_tasks_completed` and its REPEAT_PREDICATES entry from record.py; do NOT touch readiness.repeat_until_redispatch (other steps may still declare repeat_until)
  Test scenarios:
    - grep -r all_tasks_completed config/ returns zero matches
    - grep -r execute-next-task config/steps/ config/workflows/ returns zero matches
    - readiness module tests still pass (per-node repeat_until still works)
  depends: T-7, T-8

- [x] T-10: Update contracts/done-payload.md, error-recovery.md, auto-commit.md, architect-escalation.md
  Why: AC-10 — strip dangling references to execute-next-task so contracts match runtime
  Files: config/steps/contracts/done-payload.md, config/steps/contracts/error-recovery.md, config/steps/contracts/auto-commit.md, config/steps/contracts/architect-escalation.md, config/steps/CONVENTIONS.md
  Change: search-and-replace `execute-next-task` references with `execute-one-task` where the surrounding text describes the per-task contract; delete sections that only describe the all-tasks-in-one-spawn loop (e.g., done-payload.md "execute-next-task — complete all tasks, then COMPLETION")
  Test scenarios:
    - grep execute-next-task config/steps/ returns zero matches
    - done-payload.md has an execute-one-task section
    - architect-escalation.md "CONTINUE the same execute-next-task step" line is updated to reference execute-one-task
  depends: T-9

- [x] T-11: End-to-end integration test for the new implement phase
  Why: AC-11, AC-12, AC-14 — prove the whole loop works
  Files: config/tests/test-flat-task-nodes-e2e.sh
  Change: shell test that runs a synthetic feature workflow with 3 tasks through to run-phase-review; asserts (a) three separate `step_history` entries for task-T-1/T-2/T-3 with status completed, (b) `orchestrator graph` Mermaid output contains task_T_1, task_T_2, task_T_3, (c) resume scenario: mark task-T-1 completed, run `orchestrator next`, assert returned step_id == task-T-2
  Test scenarios:
    - three discrete task-node completions in step_history
    - graph renders all task-nodes
    - resume picks up at next ready task-node
  depends: T-9

## Stage 4 — Per-task telemetry

- [x] T-12: Verify per-task DuckDB rows land naturally
  Why: AC-15 — confirm the existing reconcile path keys on node id
  Files: config/scripts/orchestrator_next/upsert.py (read-only verify; only edit if rows are misnamed)
  Change: read upsert.py and the step_history → step_events insertion path; if it already keys on entry.step_id (== node id), this task is documentation-only — add a paragraph to design.md § Components confirming the path; if it does NOT, fix it to use entry.step_id
  Test scenarios:
    - after a 3-task feature run, `duckdb metrics.duckdb "SELECT step_id FROM step_events WHERE step_id LIKE 'task-%'"` returns 3 rows
    - step_events.step_id matches state_history step_id exactly
  depends: T-11

- [x] T-13: Migrate compute_resolution to read from step_history
  Why: AC-16 — telemetry source of truth shifts from tasks.md checkboxes to per-task step_history entries
  Files: config/scripts/orchestrator_next/record.py (parse_tasks, compute_resolution)
  Change: replace parse_tasks (markdown checkbox counter) with a step_history reader that counts entries whose step_id starts with `task-` and status in (completed, recovered) for tasks_completed and total task-nodes in workflow_plan[implement].nodes for tasks_total; keep parse_tasks as a deprecated shim for one cycle if any other caller exists, else delete
  Test scenarios:
    - unit test seeds step_history with two task-T-N completed entries and one failed; compute_resolution returns tasks_completed=2, tasks_total=3
    - tasks_added counter (tasks added after initial plan) is computed from expand-plan invocations (a fix-N node counts as added)
  depends: T-11

## Stage 5 — Delete tasks.md

- [x] T-14: Stop emitting tasks.md from architect step
  Why: AC-17 — telemetry no longer needs it
  Files: config/steps/design-and-draft-artifacts.yaml
  Change: remove the tasks.md write instruction, remove tasks.md from outputs and verify after_each; tasks.yaml is the sole task artifact
  Test scenarios:
    - design-and-draft-artifacts COMPLETION outputs map has no tasks.md key
    - run-phase-review does not error on missing tasks.md
  depends: T-13

- [x] T-15: Remove tasks.md from Task Format Contract and step inputs
  Why: AC-17 — strip dangling references
  Files: config/steps/contracts/artifact-formats.md, config/steps/run-phase-review.yaml, any other step contract listing tasks.md in inputs:
  Change: delete the "Task Format Contract" section from artifact-formats.md (the new "Tasks YAML Format Contract" added in T-1 supersedes it); search step contracts under config/steps/ for `tasks.md` in `inputs:` and remove
  Test scenarios:
    - grep tasks.md config/steps/ returns matches only in retro/historical comments, not in any active contract field
    - grep "Task Format Contract" config/steps/contracts/artifact-formats.md returns zero matches

- [x] T-16: Update test fixtures that still write tasks.md
  Why: AC-17 — tests are part of the surface
  Files: config/tests/test-compute-swe-metrics-per-step.sh, config/tests/test-per-agent-tokens-coverage.sh, config/tests/test-archive-merges-worktree-artifacts.sh
  Change: replace tasks.md fixture writes with tasks.yaml; update assertions that reference tasks.md to reference tasks.yaml or step_history
  Test scenarios:
    - all three tests still pass against the new contract
    - grep tasks.md config/tests/ returns matches only in archived-test fixtures (or zero)
  depends: T-13, T-14

- [x] T-17: Final sweep — grep for tasks.md and all_tasks_completed
  Why: AC-10, AC-17 — guarantee no dangling references
  Files: (verification only, may touch any file that still references tasks.md)
  Change: run `grep -rn tasks.md config/ skills/ spec/` and `grep -rn all_tasks_completed config/`; for every hit outside spec/changes/archive/, decide whether to remove or update; commit final cleanup
  Test scenarios:
    - grep tasks.md config/ skills/ shows only references inside archived feature folders
    - grep all_tasks_completed across the whole repo returns zero non-archive matches
  depends: T-14, T-15, T-16
