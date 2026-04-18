# spec.md — live-cost-probe (HL-289)

## Motivation
Users running `orchestrator next` through a multi-step change currently have no between-step visibility into running LLM spend for that change. Cost lives only in `step_events.gen_ai_usage_cost_usd` (DuckDB) and is surfaced by post-hoc analysis. Surfacing a running sum directly in the action dict closes the HL-287 dogfood loop: the caller can budget/alert inline without ad-hoc SQL.

## What Changes
- `orchestrator next` action dict gains a new key `estimated_cost_so_far` (float, USD).
- Value = `SUM(gen_ai_usage_cost_usd)` over `step_events` rows where `repo_root` + `change_id` match the current state.
- Present on every action type (`run_inline`, `run_step`, `retry_step`, `verify_phase`, `complete_workflow`, `blocked`).
- No new subcommand; no change to exit codes; no change to state.yaml schema.

## Requirements
- **FR-1**: After upsert of terminal history entries, sum `gen_ai_usage_cost_usd` for `(repo_root, change_id)` and return as a float.
- **FR-2**: Inject the value into the dispatched action dict under key `estimated_cost_so_far` before `emit_json`.
- **FR-3**: When DuckDB is unavailable (no `METRICS_DB` / `ORCHESTRATOR_HOME`), or the query fails, emit `estimated_cost_so_far: 0.0` and do not break dispatch.
- **FR-4**: When no rows exist for the change (fresh change, or all cost columns NULL), emit `0.0`.
- **FR-5**: JSON output remains deterministic (sorted keys, indent=2) — AC for HL-287 M1 must still hold.

## Acceptance Criteria
- **AC-1**: Running `orchestrator next <state>` against a fixture with two terminal entries whose `gen_ai_usage_cost_usd` = 0.01 and 0.02 produces an action dict containing `"estimated_cost_so_far": 0.03`. [traces: FR-1, FR-2]
- **AC-2**: Running against a fresh state with zero terminal entries produces `"estimated_cost_so_far": 0.0`. [traces: FR-4]
- **AC-3**: With `METRICS_DB` and `ORCHESTRATOR_HOME` unset, dispatch still succeeds and the key is present with value `0.0`. [traces: FR-3]
- **AC-4**: Existing `test_dispatch*` and `test_step_events_upsert` tests still pass; action JSON is byte-stable across runs. [traces: FR-5]

## Phase Gate Notes (light mode)
- No discovery, no UX, no phase review. Spec → design → tasks → implement → complete. TDD not required; one focused unit test per AC is sufficient.
