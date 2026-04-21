---
feature-id: durable-intent-and-resume
linear-ticket: null
---

# Discovery Brief: Durable intent + idempotent resume

## Feature Summary

Between `orchestrator next` returning an action and `orchestrator record` writing the outcome, workflow intent is not durable. A crash (driver killed, watchdog stall, machine reboot) loses the in-flight step: tokens spent, partial file changes on disk, and no terminal entry in state.yaml. The next `orchestrator next` call re-derives from scratch, causing double-execution or silent skips. This phase closes the gap with two mechanisms: (1) durable intent — `next` writes an `in_progress` row to `step_events` and an `in_progress` entry to `state.yaml.step_history` BEFORE returning the action; (2) idempotent resume — on re-entry, `next` detects the `in_progress` row and returns the same step with `is_resume: true`. This is Phase 2 of workflow-engine-as-state-machine; Phase 1 (pricing-table-in-duckdb) is complete on main.

## Personas & Actors

- **Driver loop** — the `/orchestrate` skill script that repeatedly calls `orchestrator next` and `orchestrator record`. Primary actor: calls `next`, receives an action, executes it, calls `record` to write the outcome.
- **Workflow engine** — `dispatch.py` + `record.py`. Internal actor: owns the state-machine logic.
- **DuckDB `step_events` table** — persistent store. Authoritative on reconcile per driver-locked decision #4.
- **state.yaml** — filesystem mirror of DB truth. Secondary store, always synchronized.

## Use Cases

### Happy Path

UC-1: Normal step execution — the driver calls `orchestrator next` on a fresh workflow step, so that the engine writes an `in_progress` row to DuckDB and an `in_progress` entry to `state.yaml.step_history`, then returns the step action; subsequently the driver calls `record`, which deletes the `in_progress` row from DuckDB and removes the matching entry from `state.yaml.step_history`, then appends the terminal `completed` entry.

UC-2: Resume after crash — the driver (or a new driver session) calls `orchestrator next` when a prior session crashed after `next` returned but before `record` ran, so that the engine detects the existing `in_progress` row for this `change_id`, returns the same step action with `is_resume: true` and the original `started_at` timestamp, without overwriting the in_progress row; the driver logs the resume clearly and re-executes the step from the top.

UC-3: Retry increments attempt — after a phase-review rejects `design-and-draft-artifacts`, the driver retries; the retry has `attempt=2`; the new `in_progress` row has `(step_id, attempt=2, status='in_progress')`; the prior `attempt=1` row (with `status='completed'` or `status='failed'`) coexists as a distinct PK entry and is NOT deleted.

### Error and Edge Cases

UC-E1: Attempt counter on retry — when `run-phase-review` rejects and the driver retries `design-and-draft-artifacts`, `_compute_attempt()` reads the maximum attempt in `step_history` and returns `max+1`. The `in_progress` row for attempt=2 has a distinct PK from attempt=1's `completed` row; INSERT OR REPLACE does not collide.

UC-E2: state.yaml drifts from DuckDB — if state.yaml has an `in_progress` entry but DuckDB has no matching row (e.g., DB was reset, or the write to DuckDB failed), or vice versa (DuckDB has the row but state.yaml was rolled back), DB wins. On `next` startup, the reconcile query reads DuckDB `in_progress` rows for `change_id`; state.yaml is patched to match before dispatch runs.

UC-E3: Non-step actions skip the pending write — `verify_phase` (dispatch.py:318-330), `complete_workflow` (dispatch.py:346), and `blocked` (dispatch.py:259-268) return before reaching the step-resolution branches; the pending-write code lives inside the step action return sites only (run_inline, run_step, retry_step), so non-step actions never write a pending row.

## Scope

### In Scope

- `upsert.py`: new helper `upsert_pending_step_event(db, entry, context)` that writes `status='in_progress'`, `started_at=now()`, null `ended_at`, null `usage`, null `cost_usd`; uses INSERT OR REPLACE on the full PK.
- `dispatch.py`: at the top of `dispatch()`, before any branch — query DuckDB for any `in_progress` row for the current `change_id`; if found, short-circuit and return the in-flight step with `is_resume: true`. After step-resolution branches (run_inline, run_step, retry_step), write the pending row via the new helper and append `in_progress` to `state.yaml.step_history`.
- `record.py`: terminal `record()` deletes the matching `in_progress` row from DuckDB and removes the matching `in_progress` entry from `state.yaml.step_history` (by `step_id + phase + attempt`) before appending the terminal entry.
- `bin/orchestrator` (the `next` entrypoint): open the metrics DB and pass `db` into the new pending-write call (mirrors the existing upsert loop at `bin/orchestrator:569-580`).
- Three new test files: `test_dispatch_pending_row.py`, `test_dispatch_resume.py`, `test_record_cleans_pending.py`.
- `/orchestrate` skill: log `is_resume: true` clearly when it appears in the action (even in `--auto` mode).

### Out of Scope

- Salvage path (`status: recovered`, JSONL reconstruction) — Phase 4.
- `done` verb rename — record stays named `record` this phase.
- Level-aware writes to `phase_events` / `feature_metrics` / `driver_sessions` — Phase 4.
- Phase 3 report views and retirement of `orchestrator metrics` / `cost` CLI.
- Auto-advance on `complete_workflow` phase boundaries.
- Schema changes to `step_events` — no new columns; `status='in_progress'` fits the existing `VARCHAR NOT NULL` `status` field.
- Any change to agent contract or spawn protocol.
- The `dispatch-repeat-until-honor` bug — see Constraint #7 below; this phase does NOT subsume it.

## UI Direction

N/A — no UI components.

## Key Decisions

Populated by `design-and-draft-artifacts`. Citations verified against HEAD via `rg -n` per
cycle-16 learned rule.

- **OQ-1 resolution — Option A (replace).** Delete the existing `retry_step` branch at
  `dispatch.py:270-308`. Introduce a new `resume_step` action verb with `is_resume: true` and
  the ORIGINAL attempt (unchanged, not +1). Rationale: `_compute_attempt` at
  `dispatch.py:39-51` does NOT filter by status, so overloading `retry_step` would return
  `last.attempt + 1` on resume — silently wrong. Contract (`step-dispatch.md:78-96`) and driver
  (`skills/orchestrate/SKILL.md:145`) update in lock-step. Reconcile (OQ-2) guarantees
  state.yaml reflects DB truth before dispatch, so no "offline retry" case remains that needs
  the old semantic. One mechanism, one source of truth.

- **OQ-2 resolution — Reconcile in a new module `orchestrator_next/reconcile.py`, invoked
  from `bin/orchestrator`.** `dispatch()` at `dispatch.py:248-254` is documented pure and stays
  pure. `reconcile_in_progress(state, db, context)` is called from `bin/orchestrator` inside
  the existing `_metrics_db_path` block (lines 554-582), after `ensure_schema(_db)` at 567 and
  before `dispatch()` at 585. Mutates `state.step_history` in place; no disk writes. When the
  metrics DB is absent (offline/test), reconcile is skipped.

- **OQ-3 resolution — Match on `(step_id, phase, status='in_progress')`, no attempt.** FR-1 +
  FR-6 together guarantee at most one in_progress entry per (step_id, phase) at any time.
  `record()` filters state.step_history on this three-key match in-place before the existing
  append at `record.py:489`. This removes a class of off-by-one bugs from threading `attempt`
  through both writer and reader.

- **OQ-4 resolution — Direct write from `bin/orchestrator` after `dispatch()`, gated by action
  verb.** If `action.get("action") in {"run_step", "run_inline", "resume_step"}` and `_db` is
  not None, call `upsert_pending_step_event` and mirror-append the in_progress entry to
  state.yaml via a small new helper `_append_in_progress_state_entry_if_absent` that copies
  the pre-write-bytes corruption guard pattern from `record.py:399-414`. Verb-based gating
  (not branch inference) per Constraint #10.

- **OQ-5 resolution — Tests mutate both stores directly before invoking the code under
  test.** `in_memory_db` fixture from `test_record_cost_compute.py:108-113` plus hand-built
  `State` objects (for unit tests of `reconcile_in_progress`, `upsert_pending_step_event`,
  `dispatch` resume branch, `record`). One end-to-end test uses subprocess invocation of
  `bin/orchestrator next` with `METRICS_DB=<tmp>.duckdb` for the full crash-and-resume cycle.

- **Additional finding during architecture — `_compute_attempt` hazard.** `dispatch.py:39-51`
  includes in_progress entries in its max-attempt scan. Calling it on the resume branch would
  return `last.attempt + 1`, which is retry semantics, not resume semantics. The resume branch
  MUST bypass `_compute_attempt` and use `last.attempt if last.attempt is not None else 1`
  directly. Captured in design.md § Pseudocode and tested in T-5 (AC-2 asserts attempt
  unchanged after resume).


## Constraints (10 Items Investigated)

### 1. `_find_completed_step` and dispatch.py state-machine

`dispatch.py` branches in this order (line numbers in the HEAD file under `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/dispatch.py`):

- **Line 259-268**: early return `blocked` if last entry is a blocking status.
- **Line 270-308**: existing in_progress retry path — triggers when `last.status == "in_progress" and last.ended_at is None`; returns `retry_step` action with `previous_failure: "no ended_at"`. THIS IS THE COLLISION POINT (see Open Question OQ-1).
- **Line 311-315**: `_find_completed_step` loop — skips any step with a `completed` entry; determines `next_step_id`.
- **Line 318-346**: verify_phase / complete_workflow returns.
- **Line 349-412**: step-resolution block producing `run_inline` or `run_step`.

The new resume check should go at the VERY TOP of `dispatch()`, before line 259 — query DuckDB for an `in_progress` row for `change_id`, and short-circuit to return the step with `is_resume: true`. This happens before the existing line 270-308 path, which must still handle the state.yaml `in_progress` fallback for offline/no-DB scenarios.

### 2. `step_events` PK and status column

`upsert.py:53`: `PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)`. Status is the sixth PK component. `upsert.py:37`: `status VARCHAR NOT NULL` — free string, not an enum. Existing status values written to DB: `completed`, `failed`, `blocked`, `escalate_to_architect`, `skipped` (from `_TERMINAL_STATUSES` in dispatch.py:34), plus `in_progress` appears in the state.yaml-level retry path (test fixture at `test_dispatch_allowed_tools.py:135`) but is NOT currently written to DuckDB (the upsert loop in `bin/orchestrator:565-576` filters on `_terminal_statuses = {"completed", "failed", "blocked", "escalate_to_architect"}`). Conclusion: `in_progress` is a new status value for DuckDB writes; it will not collide with existing rows.

Since `status` is in the PK, an `in_progress` row with `(step_id=X, attempt=1, status='in_progress')` and a completed row with `(step_id=X, attempt=1, status='completed')` are SEPARATE rows. INSERT OR REPLACE on the full PK means writing the pending row does NOT clobber any existing terminal row for the same attempt.

### 3. `upsert_step_event` semantics

`upsert.py:451-560`: `upsert_step_event()` uses `_INSERT_OR_REPLACE` (line 521) which maps to `INSERT OR REPLACE INTO step_events (...)`. The full PK is always provided in the parameter list (line 497-508). Semantic: INSERT OR REPLACE on the 6-column PK replaces a row only when all 6 key values match. Since `status='in_progress'` is a distinct PK component, the new `upsert_pending_step_event` helper can reuse `_INSERT_OR_REPLACE` directly with `status='in_progress'` and null values for all usage/cost columns. The function also fans out to `tool_calls` table (line 525-559); for pending rows with no tool_calls, the fan-out inserts nothing (empty `usage_tools`). The proposed new helper signature:

```python
def upsert_pending_step_event(
    db,
    *,
    repo_root: str,
    change_id: str,
    phase: str,
    step_id: str,
    attempt: int,
    agent_name: str,
    started_at: str,
) -> None:
```

It writes `status='in_progress'`, all token/cost/model columns null, no tool_calls fan-out. It must apply the slug guard on `change_id` (same as `upsert_step_event`).

### 4. `sum_cost_usd` NULL handling

`upsert.py:193-197`:
```sql
SELECT COALESCE(SUM(cost_usd), 0.0)
FROM step_events
WHERE repo_root = ? AND change_id = ?
```

SQL `SUM()` skips NULL values natively; `COALESCE` handles the all-NULL case (returns 0.0). `in_progress` rows will have `cost_usd = NULL`, which SUM skips. There is no risk of cost corruption. **Resolved constraint, not a risk.**

### 5. state.yaml `step_history` shape

`parser.py:144-159`: `_parse_history_entry()` reads `status=raw.get("status", "")` (free string), `ended_at=raw.get("ended_at") or raw.get("completed_at")` (None if both absent), `usage=raw.get("usage", {})` (defaults to `{}`). No validation rejects an entry with `status='in_progress'` or null `ended_at` or empty usage. `StepHistoryEntry.ended_at` is typed `str | None` (line 53) — None is valid. The existing retry path at `dispatch.py:272-275` already reads `last.ended_at is None` on an in_progress entry parsed from state.yaml, confirming this path works. In_progress entries are valid-but-incomplete; the `record` flow rejects malformed inputs at the payload level, not the state.yaml level.

### 6. Reconcile semantics

Driver-locked: DB wins. Strategy: at the top of `dispatch()` in `bin/orchestrator`, after `ensure_schema(db)` and before calling `dispatch(state, ...)`, query DuckDB for `SELECT phase, step_id, attempt, started_at FROM step_events WHERE repo_root=? AND change_id=? AND status='in_progress' LIMIT 1`. If a row exists: patch `state.step_history` in memory to ensure the matching entry is present (add if missing; update `started_at` if already there). If state.yaml has an `in_progress` entry but DuckDB has no matching row: strip the state.yaml in_progress entry from the in-memory state before dispatch. This reconcile logic belongs in `bin/orchestrator` as a small helper that mutates the in-memory `State` object before it is passed to `dispatch()`. The `dispatch()` function itself stays pure (no DB access per its docstring at line 254: "Does not mutate state. Does not write to state.yaml or DuckDB").

### 7. Does this phase subsume `dispatch-repeat-until-honor`?

NO. The bug trace:

1. `execute-next-task` records one task complete; `record.py` calls `_compute_next_step()` which checks `contract.repeat_until` and correctly re-emits `next_step = execute-next-task` when unchecked tasks remain. State.yaml's `next_step` is correct.
2. The driver calls `orchestrator next` again.
3. `dispatch.py:311-315`: `_find_completed_step()` checks `status == "completed"` — finds the just-recorded completed entry for `execute-next-task` — returns True — skips the step — loop advances to the next step ID in `active[]`.

The bug is in `dispatch.py` ignoring `state.next_step` and re-deriving from `_find_completed_step`. Phase 2's `in_progress` rows don't persist across task iterations: `record()` deletes the `in_progress` row when recording the terminal entry. Even with Phase 2 landed, after `record()` completes, the DuckDB pending-row check at `next` startup finds nothing — because the completed record already deleted it. The dispatch bug fires at step 3 regardless of Phase 2.

**Phase 2 does NOT subsume `dispatch-repeat-until-honor`.** The two fixes operate at different layers: Phase 2 fixes the pre-record gap (in-flight step); repeat-until fixes the post-record gap (completed step re-detection by dispatch). They must both ship; order doesn't matter.

### 8. Test fixture pattern

`test_record_cost_compute.py:108-113`: the `in_memory_db` fixture:

```python
@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()
```

The three new test files need this pattern. Each test additionally needs a `tmp_path` with a valid `state.yaml` and an optional `plan.yaml` (for dispatch tests that call `_load_plan`). The `ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE` monkeypatch pattern from `test_record_cost_compute.py:123-146` is needed for tests that go through contract loading. The `dispatch()` function is currently pure (no DB args) — the new `bin/orchestrator`-level pending-write call needs to be testable through a thin wrapper or by testing `bin/orchestrator` via subprocess; alternatively, the pending-write can be extracted to a helper function testable independently of dispatch.

### 9. Retry semantics

`record.py:419-424`: attempt number on `record()` is computed as `max(prior_attempts)+1` from history entries — it does NOT see the current `in_progress` entry as the current attempt; it counts already-recorded attempts. This means: if `attempt=1` is in_progress and never recorded (crash), on retry the driver calls `next` again, detects the `in_progress` row (attempt=1), returns `is_resume: true` with `attempt=1`. The driver records the resumed step as `attempt=1`. The `in_progress` row is deleted. `record.py:424` with an empty history computes `attempt=1` — consistent. If `record()` rejects and the driver retries (phase-review rejection path): the `attempt=1` entry is now `completed` in history; `record.py:424` returns `attempt=2`; the new `in_progress` row has `attempt=2` with a distinct PK from the `attempt=1` completed row. No collision.

### 10. Interaction with `verify_phase`, `blocked`, `complete_workflow`

The three non-step early returns in `dispatch.py` fire at lines 260-268 (`blocked`), 321-329 (`verify_phase`), and 346 (`complete_workflow`). All three return before reaching the step-resolution block (lines 349-412) where the pending-write will be inserted. The pending-write call site — the final `return action, 0` locations inside the step-resolution block — naturally excludes these non-step actions. No special guard needed.

## Build or Reuse Decision

**Reuse, with a minimal addition.** The existing INSERT OR REPLACE semantics of `upsert_step_event` (via `_INSERT_OR_REPLACE` at `upsert.py:166-190`) already handles the required insert behavior for a new PK combination. The PK already includes `status` (line 53), making `in_progress` a naturally distinct row without schema changes. The new `upsert_pending_step_event` helper is a thin wrapper around the same SQL with a fixed `status='in_progress'` and null usage fields. The migration runner (landed in Phase 1) does not need to run; no schema changes are required.

## Approaches Considered

### Approach A: Status-column row (driver-locked decision)

Write a second row for the in-flight step with `status='in_progress'` using the existing PK that includes `status`. On terminal `record`, delete this row (DELETE WHERE ... AND status='in_progress'). DuckDB's INSERT OR REPLACE handles idempotent re-writes.

**Build vs reuse**: Reuse — existing PK, existing INSERT OR REPLACE SQL, new thin helper function only.
**Pros**: No schema change. PK semantics are correct. Coexists with completed/failed rows at same attempt. SUM(cost_usd) skips NULLs cleanly (constraint #4). Parser already handles in_progress status (constraint #5).
**Cons**: A second DB row per step adds a small query on every `next` call. The resume check must query DB (adds latency path in `bin/orchestrator`).
**Effort**: Small.

### Approach B (rejected): Separate `pending_steps` table

Add a new `pending_steps` table with one row per in-flight step. Terminal `record` deletes from this table.

**Pros**: Clean separation of concerns; no coupling between in_progress rows and completed rows in the same table.
**Cons**: Requires a schema migration (new table). Adds a second write path. Violates driver-locked decision #1 ("minimal write, same primary key"). Two-table lookup on `next` startup. More JOIN complexity for any reporting query that needs to understand step state.
**Effort**: Medium.
**Rejected because**: driver-locked decision #1 explicitly forbids schema changes for this phase. The existing PK already accommodates the new status value; the extra table provides no benefit that the PK-based approach doesn't.

### Approach C (rejected): Tombstone columns on existing row (UPDATE-in-place)

Add `is_in_progress BOOLEAN` and `pending_since TIMESTAMP` columns to `step_events`. On `next`, UPDATE the existing row to set `is_in_progress=TRUE`. On `record`, UPDATE back to `is_in_progress=FALSE`.

**Pros**: One row per step always.
**Cons**: Requires schema migration (two new columns). UPDATE-in-place semantics clash with INSERT OR REPLACE pattern used everywhere. Violates driver-locked decision #3 ("delete-on-terminal over UPDATE-in-place"). Loses the clean audit trail where `in_progress` and `completed` rows are independently queryable.
**Effort**: Medium.
**Rejected because**: violates driver-locked decisions #1 and #3.

## Recommendation

Approach A (status-column row). It is the driver-locked decision and requires the smallest code surface: one new helper function in `upsert.py`, one DB query + two pending-write call sites in `bin/orchestrator`, one DELETE in `record.py`, one in-memory state.yaml patch in `bin/orchestrator`. Three new test files. No schema changes.

## CLI Surface Inventory

All callables that touch `dispatch.py`, `upsert.py`, or `record.py`:

**`bin/orchestrator` subcommands** (all in `/Users/spidey/code/orchestrator/bin/orchestrator`):
- `orchestrator next <state.yaml>` — calls `dispatch()`, calls `upsert_step_event()` + `sum_cost_usd()` for terminal history entries (lines 562-580). Will gain: pending-write call after `dispatch()` returns a step action.
- `orchestrator record <state.yaml>` — calls `record.record()` (line 529-531). Will gain: DELETE of pending row from DuckDB, removal of in_progress entry from state.yaml.step_history.
- `orchestrator cost --change-id` / `--repo` — reads `step_events` via `cost_report.py`; no writes to `upsert.py` functions. Not affected.
- `orchestrator metrics --change-id` — reads `step_events` via `metrics_report.py`. Not affected.
- `orchestrator ingest-driver --change-id --session-id` — calls `upsert_synthetic_event()` (line 348). Not affected.
- `orchestrator ingest-subagents --change-id --session-id` — calls `upsert_synthetic_event()` (line 480). Not affected.
- `orchestrator doctor` — calls `_doctor_main()`. Not affected.

**Inline scripts under `scripts/inline/`** (only these call `ensure_schema` or upsert functions):
- `scripts/inline/ingest-feature-metrics.py` — imports `ensure_schema`, `upsert_feature_metrics` (line 40). Not affected.
- `scripts/inline/mark-change-completed.sh` (embedded Python) — imports `ensure_schema`, `upsert_feature_complexity` (line 56-60). Not affected.
- `scripts/inline/check-bootstrap-state.sh`, `remove-worktree.sh`, `append-retro.sh`, `archive-completed-change.sh`, `capture-test-baseline.sh`, `preview-route.sh`, `compute-swe-metrics.sh` — bash scripts only; do not call `upsert_step_event` or `ensure_schema`. Not affected.

**Existing test files that exercise dispatch or upsert paths**:
- `test_dispatch_allowed_tools.py` — tests dispatch with `in_progress` in state history (line 135); not DB-aware.
- `test_dispatch_phase_hint.py` — tests dispatch branching; not DB-aware.
- `test_dispatch_step_context.py` — tests step_context injection; not DB-aware.
- `test_record_cost_compute.py` — uses `in_memory_db` fixture; tests `record()` with DB.
- `test_upsert.py`, `test_upsert_migration.py`, `test_upsert_turns.py` — test `upsert_step_event` directly.
- `test_repeat_until.py` — tests `_compute_next_step` via `record()`; not DB-aware.

New test files this phase:
- `test_dispatch_pending_row.py`
- `test_dispatch_resume.py`
- `test_record_cleans_pending.py`

## Technical Context

**Files to modify**:
- `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/upsert.py` — add `upsert_pending_step_event()`.
- `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py` — add pending-row DELETE and state.yaml in_progress entry removal.
- `/Users/spidey/code/orchestrator/bin/orchestrator` — add: reconcile query, pending-write call after `dispatch()`, pass db to record path.
- `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/dispatch.py` — add `is_resume: true` field to the resume short-circuit return; OR reuse the existing `retry_step` path with `is_resume` appended (see OQ-1).

**Library versions**: DuckDB (already imported; Phase 1 added pricing table). PyYAML (already used). No new dependencies.

**Integration points**:
- The existing `_terminal_statuses` set in `bin/orchestrator:565` (`{"completed", "failed", "blocked", "escalate_to_architect"}`) must not include `in_progress` — in_progress rows are written by the new helper, not by the upsert loop.
- The `sum_cost_usd()` query (upsert.py:193-197) already handles NULL cost_usd via `COALESCE(SUM(...), 0.0)` — in_progress rows with null cost_usd are safe.
- The existing in_progress path in dispatch.py (lines 270-308) reads from state.yaml, not DuckDB. After Phase 2, this path is the offline/no-DB fallback; the primary path queries DuckDB at the top of `next`.

**Phase 1 retro learned rules applied here**:
- "Design claims about caller-site capabilities must be verified by grep against HEAD" — every claim above about what `upsert_step_event`, `record`, and `dispatch` currently do is supported by line-number citations from the actual HEAD files.
- "SQL sketches must be validated against a live row from the target DB or schema file" — the SUM(cost_usd) NULL analysis is grounded in the actual `_SUM_COST_SQL` at upsert.py:193-197, not an inference.

## Open Questions

OQ-1: **Resume path vs retry path collision.** The existing dispatch.py:270-308 path already fires when `last.status == "in_progress" and last.ended_at is None`. With Phase 2 writing an `in_progress` entry to state.yaml before every step, this path fires on every re-entry — which IS the resume behavior. But it hardcodes `previous_failure: "no ended_at"` and returns `retry_step` action type. The new design wants `is_resume: true` and a clean action type. Architecture question: should the new resume check at the top of `dispatch()` (pre-line 259) replace the existing path at lines 270-308 for normal resume (DB has the in_progress row), leaving lines 270-308 as the no-DB fallback only? Or should the existing path at 270-308 be updated to return `is_resume: true` as a new field alongside the existing `retry_step` action? The answer changes what `bin/orchestrator` needs to add vs what `dispatch.py` needs to change. Architect's call.

OQ-2: **Reconcile path placement.** The reconcile logic (DuckDB in_progress row → patch in-memory State object) needs access to both the DB and the in-memory State before dispatch runs. Proposed location: inside the `_metrics_db_path`-scoped block in `bin/orchestrator` (lines 554-581), after `ensure_schema(_db)` and before `dispatch(state, ...)`. But `dispatch()` is currently a pure function (per its docstring). Should reconcile mutate the `State` object in place (acceptable since Python dataclass is mutable), or should there be a new `reconcile(state, db)` function that returns a patched copy? This is an API boundary question, not implementation — Architect's call.

OQ-3: **state.yaml `in_progress` entry removal on `record`.** When `record()` writes the terminal entry, it must also remove the matching `in_progress` placeholder from `state.yaml.step_history`. The current `record()` function only appends (line 489: `history.append(entry)`). The removal logic must match on `(step_id, phase, attempt, status='in_progress')` — but `record()` currently doesn't receive `attempt` from the caller in all cases (it computes it at line 424). Confirm: is `attempt` always available in the `record()` payload by the time Phase 2 ships? If not, removal must use a `(step_id, phase, status='in_progress')` match without `attempt` — which is safe since there should be at most one in_progress row per (step_id, phase) at any given time.

OQ-4: **`dispatch-repeat-until-honor` ordering.** Phase 2 ships before OR after the repeat-until fix; they don't conflict. However, Phase 2's resume detection (DuckDB in_progress query at `next` startup) may briefly surface an in_progress row for `execute-next-task` across task iterations if the pending-write semantics write a new in_progress on each sub-task execution and record deletes it. Confirm: is the record→next cycle for `execute-next-task` within a single repeat-iteration fast enough that no confusion arises, or does the driver need a "same-session vs different-session" distinction for resume?

