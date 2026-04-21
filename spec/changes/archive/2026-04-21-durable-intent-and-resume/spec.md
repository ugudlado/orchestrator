---
feature-id: durable-intent-and-resume
linear-ticket: null
---

# Spec: Durable intent + idempotent resume

## Motivation

Between `orchestrator next` returning an action and `orchestrator record` writing the outcome,
workflow intent is not durable. A crash (driver killed, watchdog stall, reboot, `kill -9` of the
spawned agent) loses the in-flight step: tokens are spent on the sub-agent, partial artifacts may
have been written to disk, but no terminal entry lands in state.yaml and no row lands in DuckDB
`step_events`. On the next `orchestrator next`, dispatch re-derives from scratch. Depending on
what the crashed step had written, this causes either double-execution (fresh spawn, same step,
new cost) or silent skips (fresh spawn moves past the step because a later heuristic treats the
missing record as "never started").

This feature closes that gap with two coordinated mechanisms:

1. **Durable intent** — `orchestrator next` writes an `in_progress` row to DuckDB `step_events`
   AND an `in_progress` entry to `state.yaml.step_history` BEFORE the action is returned to the
   driver.
2. **Idempotent resume** — on re-entry after a crash, `orchestrator next` detects the
   `in_progress` row, reconciles state.yaml against DB (DB wins), and returns a `resume_step`
   action with the ORIGINAL `attempt` and `is_resume: true`. The driver re-executes the step
   from the top without bumping the attempt number.

This is Phase 2 of `workflow-engine-as-state-machine`. Phase 1 (pricing-table-in-duckdb) is
shipped on main. Phase 1's retro surfaced two recurrences tied to the same missing-intent class
of failure: the workflow-init stall (the driver could not tell whether workflow-init had ever
fired) and `dispatch-repeat-until-honor` recurrence #2. This phase fixes the first; the second
is out of scope (independent bug; Constraint #7 of discovery.md proves Phase 2 does not subsume
it).

## In Scope

- A new helper `upsert_pending_step_event(db, *, repo_root, change_id, phase, step_id, attempt,
  agent_name, started_at)` in `config/scripts/orchestrator_next/upsert.py`. Writes one row with
  `status='in_progress'`, all token/cost/model columns NULL, no tool_calls fan-out, reusing the
  existing `_INSERT_OR_REPLACE` SQL (upsert.py:166-190).
- An in-memory reconcile helper in `config/scripts/orchestrator_next/reconcile.py` (new module)
  exposing `reconcile_in_progress(state, db, context) -> None`. Mutates `state.step_history` in
  place before `dispatch()` runs. DB is authoritative.
- A new `resume_step` action verb in `dispatch.py`, emitted when the last history entry for the
  current phase is `status='in_progress'`. Returns the ORIGINAL attempt number (not
  `_compute_attempt`'s +1).
- Replacement of the existing `retry_step` branch in `dispatch.py:270-308`. The old branch is
  deleted. There is no migration fallback: after Phase 2 reconcile runs, any surviving
  `in_progress` entry in state.step_history is a legitimate resume.
- In `bin/orchestrator`: after `dispatch()` returns, if `action.action ∈ {run_step, run_inline,
  resume_step}`, call `upsert_pending_step_event` and append/ensure a matching `in_progress`
  entry in state.yaml.step_history. This write is gated by action verb, not by branch inference.
- In `config/scripts/orchestrator_next/record.py`: terminal `record()` deletes the matching
  in_progress row from DuckDB and mutates state.yaml.step_history to remove the existing
  in_progress entry for the same `(step_id, phase)` BEFORE appending the terminal entry.
- Contract update: `config/steps/contracts/step-dispatch.md` — replace the `retry_step` section
  with a `resume_step` section documenting the new action, its fields, the `is_resume: true`
  marker, and the attempt-unchanged semantic.
- Driver update: `skills/orchestrate/SKILL.md` — replace the `retry_step` handler line with a
  `resume_step` handler that logs the resume clearly and executes the step (same machinery as
  `run_step` / `run_inline`). Must log even in `--auto` mode per discovery In Scope.
- Five new test files: `test_upsert_pending.py`, `test_dispatch_pending_row.py`,
  `test_dispatch_resume.py`, `test_reconcile_in_progress.py`, `test_record_cleans_pending.py`.
  Five existing test artifacts retargeted from the retired `retry_step` branch to the new
  `resume_step` verb: `test_dispatch_allowed_tools.py` (actual assertion at lines 208-220),
  `test_orchestrator_next.py`, `test_attempt_counting.py`, `test_cost_so_far.py`, and the
  golden fixture `golden/state-in-progress-no-ended.json`.

## Out of Scope

- Salvage path (`status: recovered`, JSONL reconstruction on orphan in_progress rows) — Phase 4.
- `done` verb rename (record stays `record` this phase).
- Level-aware writes to `phase_events` / `feature_metrics` / `driver_sessions` — Phase 4.
- Phase 3 report views and retirement of `orchestrator metrics` / `cost` CLI.
- Auto-advance on `complete_workflow` phase boundaries.
- Schema migrations to `step_events`: no new columns; `status='in_progress'` fits the existing
  `VARCHAR NOT NULL` field.
- Any change to agent contract or spawn protocol.
- The `dispatch-repeat-until-honor` bug (Constraint #7 in discovery.md proves Phase 2 does not
  subsume it; ordering with that fix is independent).

## Functional Requirements

- **FR-1** — `orchestrator next` writes an `in_progress` row to DuckDB `step_events` for the
  returned step BEFORE printing the action JSON to stdout. The write is idempotent: re-running
  `next` for the same (repo_root, change_id, phase, step_id, attempt, 'in_progress') PK replaces
  the row but does not duplicate it. [traces: UC-1, UC-2]
- **FR-2** — `orchestrator next` appends (or preserves) an `in_progress` entry to
  `state.yaml.step_history` matching the DuckDB row. If reconcile found a pre-existing entry, no
  duplicate is appended. [traces: UC-1, UC-E2]
- **FR-3** — On `next` re-entry when DuckDB has an `in_progress` row for the current
  `change_id`, the CLI reconciles state.yaml to match DB and returns a `resume_step` action with
  `is_resume: true`, the ORIGINAL attempt number (unchanged, not incremented), the original
  `started_at` timestamp, and all fields required for the driver to re-spawn the step (agent,
  instruction, rules, inputs, env, step_context). [traces: UC-2]
- **FR-4** — On `next` re-entry when state.yaml has an `in_progress` entry for the current phase
  but DuckDB does not, reconcile strips the stale entry from state.step_history before dispatch;
  dispatch then proceeds with fresh step selection as if the entry had never existed. DB wins.
  [traces: UC-E2]
- **FR-5** — On `next` re-entry when DuckDB has an `in_progress` row but state.yaml does not,
  reconcile appends an `in_progress` entry to state.step_history derived from the DB row
  (phase, step_id, attempt, started_at, agent_name) before dispatch. [traces: UC-E2]
- **FR-6** — `orchestrator record` for any terminal status deletes the matching
  `(repo_root, change_id, phase, step_id, attempt, status='in_progress')` row from DuckDB
  `step_events`. The DELETE is parameterised; status='in_progress' is the filter. Missing row is
  not an error. [traces: UC-1]
- **FR-7** — `orchestrator record` removes the in_progress entry for `(step_id, phase)` from
  state.yaml.step_history before appending the terminal entry. Matching uses
  `(step_id, phase, status='in_progress')` — attempt is NOT in the match key. The
  one-in-progress-per-(step_id, phase) invariant (FR-1 + FR-6) guarantees uniqueness.
  [traces: UC-1]
- **FR-8** — Non-step actions (`verify_phase`, `complete_workflow`, `blocked`) do NOT trigger a
  pending-row write. Gating is by `action.action` value, not by inference about which dispatch
  branch returned. [traces: UC-E3]
- **FR-9** — Retry with `attempt=2` (phase-review rejection path) writes an `in_progress` row
  with a distinct PK from the terminal `attempt=1` row. Both rows coexist; the prior completed
  row is NOT deleted by the attempt=2 pending write. [traces: UC-3, UC-E1]
- **FR-10** — The `resume_step` action MUST include `is_resume: true` in the JSON; the driver
  log line MUST include this fact even under `flags.auto = true`. [traces: UC-2]

## Non-Functional Requirements

- **NFR-1** — The pending-row write and the reconcile query together add at most 5 ms to
  end-to-end `orchestrator next` latency on a workstation-class machine running DuckDB against a
  `step_events` table of up to 10,000 rows. This is a production target, not a microbenchmark:
  the measurement is end-to-end `next` invocation wall-clock, not a tight-loop call count.
- **NFR-2** — Resume detection and reconcile survive `kill -9` delivered at any point between
  `next` returning and `record` writing the terminal entry. No partial state (DB row exists but
  state.yaml missing the entry, or vice versa) can break the subsequent `next` invocation: FR-4
  and FR-5 together ensure reconcile produces a consistent in-memory `State` regardless of which
  store is out of sync.
- **NFR-3** — All SQL in the new helper, the reconcile query, and the terminal DELETE uses
  parameterised `duckdb.execute(sql, params)`. No string interpolation of user-controlled
  values. Slug guard on `change_id` is applied before every query that embeds it.
- **NFR-4** — Test coverage ≥ 90 % on modified lines in `upsert.py`, `record.py`, `dispatch.py`,
  `bin/orchestrator`, and the new `reconcile.py`. Measured via the existing pytest + coverage
  wiring. Five new test files plus five retargeted test artifacts (see In Scope).
- **NFR-5** — The one-in-progress-per-(step_id, phase) invariant stated in FR-7 is an explicit
  design contract. A test MUST assert that the state after `next`→`record`→`next` for the same
  step leaves at most one in_progress row in DuckDB at any observation point.

## Acceptance Criteria

- **AC-1** — After `orchestrator next` returns a step action (`run_step` / `run_inline` /
  `resume_step`) for a fresh or in-flight step, a row with
  `(repo_root=X, change_id=Y, phase=Z, step_id=S, attempt=A, status='in_progress')` exists in
  DuckDB `step_events` and a corresponding entry exists in `state.yaml.step_history`.
  Verify: `test_dispatch_pending_row.py::test_next_writes_in_progress_row_and_state_entry`.
  [traces: FR-1, FR-2, UC-1]
- **AC-2** — Given DuckDB already contains an in_progress row for `(change_id, phase, step_id,
  attempt=1)` and state.yaml has the matching entry, a subsequent `orchestrator next` returns
  `action='resume_step'` with `is_resume: true`, `attempt=1` (unchanged), and the same
  `started_at` as the DB row. Verify:
  `test_dispatch_resume.py::test_resume_returns_same_attempt_and_is_resume_flag`.
  [traces: FR-3, UC-2]
- **AC-3** — After `orchestrator record` terminates a step, the matching in_progress row is
  gone from DuckDB (`SELECT COUNT(*) … WHERE status='in_progress' AND step_id=… = 0`) and the
  in_progress entry is gone from `state.yaml.step_history` (only the new terminal entry
  remains). Verify:
  `test_record_cleans_pending.py::test_terminal_record_deletes_pending_row_and_state_entry`.
  [traces: FR-6, FR-7, UC-1]
- **AC-4** — When state.yaml has an orphan in_progress entry but DuckDB has no matching row,
  `orchestrator next` reconciles by stripping the entry; dispatch proceeds as if the entry was
  absent (returns the expected next pending step, NOT `resume_step`). Verify:
  `test_reconcile_in_progress.py::test_yaml_orphan_stripped_when_db_empty`. [traces: FR-4,
  UC-E2]
- **AC-5** — When DuckDB has an in_progress row but state.yaml lacks the matching entry,
  `orchestrator next` reconciles by appending the entry; dispatch returns `resume_step`.
  Verify: `test_reconcile_in_progress.py::test_db_row_materialises_yaml_entry`.
  [traces: FR-5, UC-E2]
- **AC-6** — `orchestrator next` returning `verify_phase`, `complete_workflow`, or `blocked`
  does NOT write an in_progress row or state entry. Verify:
  `test_dispatch_pending_row.py::test_non_step_actions_skip_pending_write`. [traces: FR-8,
  UC-E3]
- **AC-7** — After a phase-review rejection and retry, the `attempt=2` in_progress row coexists
  with the `attempt=1` terminal row in DuckDB as distinct PK entries; neither is clobbered.
  Verify: `test_dispatch_pending_row.py::test_retry_attempt_two_coexists_with_attempt_one`.
  [traces: FR-9, UC-3, UC-E1]
- **AC-8** — `sum_cost_usd(db, context)` returns the same value whether or not in_progress rows
  exist for the change_id, because NULL `cost_usd` is skipped by the existing `COALESCE(SUM(…),
  0.0)` query at `upsert.py:193-197`. Verify:
  `test_record_cleans_pending.py::test_in_progress_rows_do_not_affect_cost_sum`. [traces: NFR-3
  indirectly; explicit invariant check]
- **AC-9** — The `orchestrate` skill logs a line containing the literal substring `RESUMING
  step` on stderr when the action has `is_resume: true`, including when `flags.auto = true`.
  Verify: a driver integration test that captures stderr and asserts `"RESUMING step" in
  stderr`; passes in both auto and interactive modes. [traces: FR-10, UC-2]

- **AC-10** — Across two full `next`→`record`→`next`→`record` cycles for two different steps
  in the same phase, DuckDB `step_events` has zero rows with `status='in_progress'` at the
  final observation point, proving the one-in-progress-per-(step_id, phase) invariant holds
  across the lifecycle, not just a single cycle. Verify:
  `test_record_cleans_pending.py::test_two_cycle_lifecycle_leaves_no_in_progress_rows`.
  [traces: FR-6, FR-7, NFR-5]

## Alternatives Considered

- **Separate `pending_steps` table** — rejected in discovery (Approach B). Requires a schema
  migration and a second query path on every `next` startup for no benefit over the status-PK
  approach.
- **`is_in_progress BOOLEAN` column with UPDATE-in-place** — rejected in discovery (Approach C).
  Violates the delete-on-terminal driver-locked decision and the existing INSERT OR REPLACE
  pattern.
- **OQ-1 Option B (coexist): keep the state.yaml retry path at `dispatch.py:270-308` and layer
  the DB resume on top** — rejected. Two mechanisms that must agree will drift. Also, the old
  path calls `_compute_attempt` which would return `last.attempt + 1`, which is wrong for
  resume semantics (resume keeps the same attempt). Keeping the old path means silently wrong
  attempt numbers on any offline/no-DB resume.
- **OQ-1 Option C (deprecate-in-place, delete in Phase 4)** — rejected. The deprecated code
  remains callable; the same attempt-bump bug surfaces if Phase 4 slips. Replace now.
- **Enrich `retry_step` with `is_resume: true`** — rejected. The contract
  (`step-dispatch.md:80-91`) specifies `retry_step` returns an incremented attempt; downstream
  log and driver behavior treats it as "prior attempt failed → bump." Overloading it with
  resume semantics (same attempt, clean re-exec) would conflate two distinct workflow events.

## Traceability

| UC | FR | AC |
|----|----|----|
| UC-1 | FR-1, FR-2, FR-6, FR-7 | AC-1, AC-3 |
| UC-2 | FR-3, FR-10 | AC-2, AC-9 |
| UC-3 | FR-9 | AC-7 |
| UC-E1 | FR-9 | AC-7 |
| UC-E2 | FR-4, FR-5 | AC-4, AC-5 |
| UC-E3 | FR-8 | AC-6 |
| (invariant) | FR-6, FR-7, NFR-5 | AC-10 |
