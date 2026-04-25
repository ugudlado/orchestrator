---
feature-id: done-verb-level-aware-writes
linear-ticket: N/A
---

# Specification: Done verb + level-aware writes (Phase 4 of workflow-engine-as-state-machine)

## Motivation

Today's metrics pipeline has three CLI verbs (`record`, `ingest-driver`, `ingest-subagents`) that write at different times in the complete phase. The two `ingest-*` steps are fail-soft inline scripts — when they silently skip, phase- and feature-level rows in DuckDB go missing and downstream views show partial data. The naming is also misleading: `record` writes step-level rows but the verb sounds general, while phase and feature aggregates require remembering separate verbs.

Phase 4 collapses this into one verb (`done`) that handles all step outcomes (`completed`, `recovered`, `abandoned`) and detects phase/feature boundaries internally. When the boundary is hit, `done` writes the phase row and (at feature end) the feature-level driver-session row inside the same DuckDB transaction as the step row. No silent skips, no inline scripts, one mental model for drivers.

The rename is bootstrap-hazardous — the workflow running this feature uses `orchestrator record` to advance its own steps. Phase 4 mitigates this with a staged alias migration: add `done` as a second entry point first, migrate callers second, deprecate `record` third.

## What Changes

- New CLI verb `orchestrator done` added to `bin/orchestrator`. Initially aliases to the existing `record` entry point so both verbs route to the same code path.
- New `payload.status` dispatch in `record.py`'s `record()` function: `completed` (current behavior), `recovered` (writes step row with `status: recovered`, no boundary trigger), `abandoned` (writes step row with `status: abandoned`, sets `state.yaml.status: blocked`).
- New DuckDB tables `phase_events` and `driver_sessions` created via migration `0003_phase_events_driver_sessions.sql`.
- Boundary detection inside `record()`: reads `state.yaml.workflow_plan[<phase>].active` (already populated by the engine), compares the current `step_id` against the last entry in that list. If equal AND `status == completed`, this is the phase boundary.
- Feature boundary: when the phase boundary is hit AND the phase is the last phase in `workflow_plan` keys order, this is also the feature boundary.
- Atomic boundary write: `step_events` upsert + `phase_events` insert (and `driver_sessions` insert at feature boundary) execute inside one DuckDB transaction.
- `ingest-driver-auto` and `ingest-subagents-auto` removed from `_complete-phase.yaml` step list once boundary write is verified. Their session-id resolution and JSONL parsing logic moves into two helpers that `done` calls at the feature boundary: (a) `_resolve_driver_session()` absorbs `_ingest_driver_main` (driver-loop synthetic row + driver_sessions row); (b) `_resolve_subagent_rows()` + `_write_subagent_events()` absorb `_ingest_subagents_main` — they discover sub-agent JSONLs under `~/.claude/projects/<slug>/<session>/subagents/`, read each `agent-<id>.meta.json` sidecar for agentType, call `extract_agent_usage()`, and insert one synthetic `step_events` row per sub-agent (`agent` field set to the subagent name, `step_id` = `subagent-<id>`, `phase` = `meta`) so the existing `agent_report` view continues to receive per-subagent attribution rows.
- All production callers migrated from `orchestrator record` → `orchestrator done`.
- `scripts/m8-gates.sh:45` banner assertion updated to accept either verb during transition, then asserts `done` once the rename is complete.
- `tests/test_prose_contracts.py:135–136` string assertions updated to assert `orchestrator done`.
- Python module name (`orchestrator_next/record.py`) stays unchanged — only the CLI verb is renamed. This is the smallest blast radius: 6 test files keep their import paths, `bin/orchestrator:84,170` keep their `_compute_cost_usd` imports.

## Requirements

### Functional

1. **FR-1**: `bin/orchestrator` MUST accept `done` as a CLI verb with the same `<state.yaml>` argument and stdin JSON-payload contract as `record`. Both verbs route to `orchestrator_next.record.main` during the alias period.
2. **FR-2**: The `record()` function MUST dispatch on `payload.status`. For `completed`, current behavior is preserved. For `recovered`, the step_events row is written with `status: recovered` and boundary detection is SKIPPED. For `abandoned`, the step_events row is written with `status: abandoned` and `state.yaml.status` is set to `blocked`; boundary detection is SKIPPED.
3. **FR-3**: Migration `0003_phase_events_driver_sessions.sql` MUST create two tables. `phase_events` keyed by `(repo_root, change_id, phase, attempt)` with token/cost/duration aggregates and `step_count`. `driver_sessions` keyed by `(repo_root, change_id, session_id)` with the same fields `ingest-driver-auto` produces today (session_id, total tokens, cost_usd, model, started_at, ended_at).
4. **FR-4**: Boundary detection MUST read `state.yaml.workflow_plan[current_phase].active`. The current step is the phase boundary when its `step_id` equals the last element of that list AND `status == "completed"`. The current step is also the feature boundary when its phase equals the last key in `workflow_plan`.
5. **FR-5**: At the phase boundary, `done` MUST write a `phase_events` row aggregated from all `step_events` rows for `(repo_root, change_id, current_phase)`. The aggregate write and the triggering `step_events` upsert MUST execute inside one DuckDB transaction (BEGIN / COMMIT / ROLLBACK on failure).
6. **FR-6**: At the feature boundary, in addition to the `phase_events` write, `done` MUST write one `driver_sessions` row using session-id resolution adapted from `_ingest_driver_main` (env var → JSONL scan fallback). The same transaction wraps all writes.
6a. **FR-6a**: At the feature boundary, `done` MUST insert one synthetic `step_events` row per sub-agent discovered in the JSONL transcripts under `~/.claude/projects/<slug>/<session>/subagents/`, with `agent` set to the subagent name (read from `agent-<id>.meta.json` sidecar's `agentType`, falling back to `subagent-unknown`), `step_id` = `subagent-<id>`, `phase` = `meta`, and `cost_usd` computed via `_compute_cost_usd`. These inserts MUST execute inside the same DuckDB transaction as the step / phase / driver_session writes. Per-subagent row construction MUST be fail-soft: a parse error on one subagent JSONL skips that subagent (logged to stderr) without aborting the transaction. The discovery + JSONL parsing pass MUST run BEFORE the `BEGIN` to keep the transaction window short — only the inserts are inside the transaction. Idempotency MUST match the legacy behavior: if a `step_events` row already exists for `(repo_root, change_id, phase='meta', step_id='subagent-<id>', attempt=1)` with non-zero `input_tokens`, skip the insert.
7. **FR-7**: `_complete-phase.yaml` MUST remove `ingest-driver-auto` and `ingest-subagents-auto` from its `steps:` list. The corresponding step contracts under `config/steps/` MUST be deleted. The inline scripts (`scripts/inline/ingest-driver-auto.py`, `scripts/inline/ingest-subagents-auto.py`) MUST be deleted.
8. **FR-8**: `bin/orchestrator` MUST remove `ingest-driver` and `ingest-subagents` from its accepted verb list and from the usage banner. The `_ingest_driver_main` and `_ingest_subagents_main` functions MUST be deleted. The `_compute_cost_usd` import in those functions migrates to live inside the new `_resolve_driver_session` helper in `record.py`.
9. **FR-9**: All production prose references to `orchestrator record` MUST be updated to `orchestrator done`: `skills/orchestrate/SKILL.md` (3 dispatch references on lines 88, 135, 139, 174), `agents/developer.md:213`, `agents/workflows-init.md:90`, `CLAUDE.md:33`, `config/steps/ingest-feature-metrics.yaml:27`.
10. **FR-10**: `scripts/m8-gates.sh:45` MUST be updated to assert `orchestrator done` is present in the banner. Stage A allows either verb (gate temporarily asserts presence of one of `done|record`); Stage C tightens to `done` only.
11. **FR-11**: `config/scripts/orchestrator_next/tests/test_prose_contracts.py:135–136` MUST assert `orchestrator done` after Stage B is complete. Tests covering `agents/developer.md` and `agents/workflow-init.md` MUST pass against `done`.
12. **FR-12**: After Stage B verification, `record` is REMOVED from the `bin/orchestrator` usage banner but kept routing internally to `done` for one cycle as a silent fallback (Stage C). The accepted-verb tuple becomes `("next", "done", "record", "doctor")` with `record` undocumented.

### Non-Functional

1. **NFR-1**: The bootstrap hazard MUST NOT regress. After Stage A is shipped, `orchestrator record` continues to advance step_history identically to today. After Stage B is shipped, both `record` and `done` continue to work. After Stage C, `record` still routes silently. At no point during the migration may an in-flight workflow lose the ability to record steps.
2. **NFR-2**: The boundary write transaction MUST be atomic — either all rows (step + phase + optional driver_session) commit, or none do. Partial success is forbidden because the pipeline relies on phase rows existing iff the corresponding step rows exist.
3. **NFR-3**: Boundary write failure is FATAL (non-zero exit). This is a deliberate departure from `record.py`'s current fail-soft DB pattern. The value proposition of `done` is consistency; silent boundary loss reproduces the exact bug Phase 4 fixes. Step-row write remains fail-soft (current behavior preserved) — only boundary writes are fatal.
4. **NFR-4**: Performance impact under production load: the boundary-detection path adds at most one list lookup against `workflow_plan` (already in memory after parsing) and one DuckDB SELECT aggregate per phase boundary. Target: p99 < 50ms added latency at phase boundaries on a typical feature (≤30 step_events rows aggregated). Non-boundary calls add only the workflow_plan lookup (target: p99 < 1ms added overhead).
5. **NFR-5**: All new SQL MUST use parameterised `db.execute(sql, params)` calls. No string interpolation. `change_id` MUST be re-validated against the existing slug regex (`^[a-z0-9][a-z0-9-]*$`) before any new INSERT.

## Architecture

| File | Change |
|------|--------|
| `bin/orchestrator` | Stage A: add `done` to accepted verbs (line 334) and dispatch (after line 351). Stage C: remove `ingest-driver`/`ingest-subagents` from accepted verbs and banner; remove `_ingest_driver_main` / `_ingest_subagents_main` (lines 53-265); banner line 44 changes to `orchestrator done <state.yaml>`. |
| `config/scripts/orchestrator_next/record.py` | Add `payload.status` dispatch in `record()`. Add `_detect_boundary(state, payload)` helper. Add `_write_phase_event(db, ...)` helper. Add `_resolve_driver_session(state, change_id)` helper (absorbs `_ingest_driver_main` logic — driver-loop synthetic row + driver_sessions row). Add `_resolve_subagent_rows(repo_root, change_id, session_id)` helper (absorbs `_ingest_subagents_main` discovery + JSONL parse, returns a list of (agent_name, step_id, usage) tuples — fail-soft per row). Add `_write_subagent_events(db, repo_root, change_id, rows)` helper (inserts one synthetic step_events row per tuple via `upsert_synthetic_event`, with idempotency check). Wrap `step_events` upsert + phase_events insert + driver_sessions insert + per-subagent step_events inserts in one BEGIN/COMMIT block when feature boundary detected. JSONL discovery and parsing run OUTSIDE the transaction; only inserts are inside. Update CLI usage string in `main()` to advertise `done` instead of `record`. |
| `config/scripts/orchestrator_next/upsert.py` | No DDL changes here — new tables are added via migration `0003`. Add helper functions `upsert_phase_event(db, ...)` and `upsert_driver_session(db, ...)` mirroring `upsert_step_event` style. |
| `config/scripts/orchestrator_next/migrations/0003_phase_events_driver_sessions.sql` | NEW — DDL for `phase_events` and `driver_sessions`. Style matches 0001 (NOT NULL columns, PRIMARY KEY clause, IF NOT EXISTS). |
| `config/workflows/_complete-phase.yaml` | Remove `ingest-driver-auto` and `ingest-subagents-auto` from `steps:`. |
| `config/steps/ingest-driver-auto.yaml` | DELETE. |
| `config/steps/ingest-subagents-auto.yaml` | DELETE. |
| `scripts/inline/ingest-driver-auto.py` | DELETE. |
| `scripts/inline/ingest-subagents-auto.py` | DELETE. |
| `skills/orchestrate/SKILL.md` | Replace 4 occurrences of `orchestrator record` (lines 88, 135, 139, 174) with `orchestrator done`. |
| `agents/developer.md` | Replace `orchestrator record` (line 213) with `orchestrator done`. |
| `agents/workflow-init.md` | Replace `orchestrator record` (line 90) with `orchestrator done`. |
| `CLAUDE.md` | Replace `orchestrator record` (line 33) with `orchestrator done`; update wording to reflect new verb. |
| `config/steps/ingest-feature-metrics.yaml` | Replace `orchestrator record` (line 27) with `orchestrator done` in instruction prose. |
| `scripts/m8-gates.sh` | Line 45: assert `done` in banner. Stage-A interim allows `done|record`; Stage C is `done` strict. |
| `config/scripts/orchestrator_next/tests/test_prose_contracts.py` | Lines 135–136: replace `orchestrator record` with `orchestrator done`. |

## Test Strategy

### Test File Paths

| Component | Test file |
|-----------|-----------|
| `record.py::record()` status dispatch | `config/scripts/orchestrator_next/tests/test_done_status_dispatch.py` (NEW) |
| `record.py::_detect_boundary()` | `config/scripts/orchestrator_next/tests/test_boundary_detection.py` (NEW) |
| `record.py::_write_phase_event()` + transactional commit | `config/scripts/orchestrator_next/tests/test_phase_boundary_write.py` (NEW) |
| `record.py::_resolve_driver_session()` + feature boundary write | `config/scripts/orchestrator_next/tests/test_feature_boundary_write.py` (NEW) |
| `bin/orchestrator` `done` verb dispatch | `config/scripts/orchestrator_next/tests/test_done_verb_alias.py` (NEW) |
| Migration `0003` schema | `config/scripts/orchestrator_next/tests/test_migration_0003.py` (NEW) |
| Prose contract assertions | `config/scripts/orchestrator_next/tests/test_prose_contracts.py` (UPDATE existing) |
| m8-gates banner | `scripts/m8-gates.sh` self-runs against `bin/orchestrator` (no separate test) |

### Coverage Targets

90% overall for new code in `record.py` and `upsert.py`. 100% on `_detect_boundary` (small, branchy, critical). 100% on the transactional commit path (failure → ROLLBACK paths must each have a test).

### Key Test Scenarios

- `done` with `status: completed` on non-boundary step → step_events written, no phase_events row.
- `done` with `status: completed` on phase-boundary step → step_events + phase_events both written; one DuckDB SELECT confirms one row in each.
- `done` with `status: completed` on feature-boundary step → step_events + phase_events + driver_sessions all written; one DuckDB SELECT confirms one row in each.
- `done` with `status: recovered` on phase-boundary step → step_events written with status=recovered, NO phase_events row.
- `done` with `status: abandoned` → step_events written with status=abandoned, state.yaml.status set to blocked, NO phase_events row.
- Boundary write failure (mock `_write_phase_event` raises) → exit code is non-zero, step_events row is ALSO rolled back (verified by absence in DuckDB).
- `record` verb continues to dispatch correctly during Stage A and Stage C (compatibility test).
- Migration 0003 idempotent: applying twice creates no duplicates and inserts no new `schema_migrations` row on second pass.
- `_resolve_driver_session` reproduces the same session_id and cost_usd as the legacy `_ingest_driver_main` for an archived feature (fixture-based regression).

## Acceptance Criteria

- AC-1: Given an in-flight feature whose state.yaml records steps via `orchestrator record`, when Stage A is shipped (alias added), then `orchestrator record` continues to advance step_history without behavior change. [traces: UC-EN-1]
- AC-2: Given a payload with `status: completed` for a step that is the last in `workflow_plan[phase].active`, when `orchestrator done` runs, then a `step_events` row AND a `phase_events` row are visible in DuckDB and exit code is 0. [traces: UC-1, UC-4]
- AC-3: Given a payload with `status: completed` for a step that is NOT the last in its phase, when `orchestrator done` runs, then a `step_events` row is written and NO `phase_events` row is written. [traces: UC-EN-2]
- AC-4: Given a payload with `status: recovered` for any step, when `orchestrator done` runs, then a `step_events` row is written with `status=recovered` and no boundary write occurs even if the step is the phase boundary. [traces: UC-2]
- AC-5: Given a payload with `status: abandoned`, when `orchestrator done` runs, then a `step_events` row is written with `status=abandoned`, `state.yaml.status` is set to `blocked`, and no boundary write occurs. [traces: UC-3]
- AC-6: Given the feature-boundary step (last step of last phase) with `status: completed`, when `orchestrator done` runs, then `step_events` + `phase_events` + `driver_sessions` rows are all visible in DuckDB and exit code is 0. [traces: UC-1, UC-4]
- AC-6a: Given the feature-boundary step with `status: completed` AND ≥1 sub-agent JSONL present under `~/.claude/projects/<slug>/<session>/subagents/`, when `orchestrator done` runs, then one additional synthetic `step_events` row per sub-agent is committed in the same transaction, with `agent` = subagent name (from `agent-<id>.meta.json`), `phase` = `meta`, `step_id` = `subagent-<id>`, and the `agent_report` view returns those rows aggregated by `agent_name`. A malformed sub-agent JSONL skips only that row (logged to stderr) and does not roll back the transaction. [traces: UC-1, UC-4]
- AC-7: Given the boundary write fails (DuckDB lock or schema error), when `orchestrator done` runs, then exit code is non-zero AND no `step_events` row is left committed for that call (atomic ROLLBACK). [traces: UC-EN-3]
- AC-8: Given Stage B is complete (all callers migrated), when `m8-gates.sh` runs, then it asserts `orchestrator done` is in the banner and passes. [traces: UC-1]
- AC-9: Given migration `0003` has not run, when `ensure_schema()` is called, then `phase_events` and `driver_sessions` tables are created and `schema_migrations` records `0003_phase_events_driver_sessions.sql`. [traces: UC-4]
- AC-10: Given Stage C is complete, when `_complete-phase.yaml` is read, then `ingest-driver-auto` and `ingest-subagents-auto` are absent from the `steps:` list, and `bin/orchestrator` does not advertise either verb. [traces: UC-1]

## Alternatives Considered

**Alternative 1: Atomic single-pass rename (Approach A from discovery)**
Rejected. The bootstrap hazard makes single-pass rename dangerous: the workflow running this feature uses `orchestrator record` to advance itself. Any missed caller mid-rename breaks the workflow.

**Alternative 2: Keep `record` permanently, add `done` as a separate verb (Approach C from discovery)**
Rejected. Two verbs with overlapping semantics permanently contradicts the consolidation goal stated in the backlog.

**Alternative 3: Rename the Python module (`record.py` → `done.py`)**
Rejected for Phase 4 scope. Renaming the module forces 6 test files and `bin/orchestrator` to update import paths in lockstep with the verb rename. The semantic mismatch (module called `record`, verb called `done`) is acceptable and well-precedented (e.g., Linux `man` command's source file is `man.c`). Module rename can be a future cosmetic cleanup.

**Alternative 4: Read plan.yaml directly for boundary detection**
Rejected. `state.yaml.workflow_plan[phase].active` is already populated and parsed at every `record` call. Reading `plan.yaml` adds a file-system dependency and a path-resolution failure mode for no semantic gain.

## Impact

- Breaking changes: none during Stages A and B. After Stage C, callers explicitly using `orchestrator ingest-driver` or `orchestrator ingest-subagents` from outside this repo will break — verified that no such callers exist (grep confirms the only callers are `scripts/inline/ingest-*-auto.py`, both of which are deleted in this feature).
- Migration: existing DuckDB databases gain `phase_events` and `driver_sessions` tables on next `ensure_schema()` call via `_run_migrations`. Backfill of historical data is OUT OF SCOPE — only data flowing through `done` after the migration populates the new tables.
- Affected areas: complete phase step list (shrinks by 2 steps), CLI verb surface (gains `done`, loses 2 ingest verbs), agent prose, gate scripts.

## Decisions

- Module name stays `record.py`: minimizes blast radius (no test import churn, no cross-module import update at `bin/orchestrator:84,170`).
- Boundary detection source: `state.yaml.workflow_plan[phase].active` (already in memory).
- Boundary write atomicity: fatal-on-failure (BEGIN/COMMIT/ROLLBACK around step + phase writes).
- Step write fail-soft is preserved when no boundary is involved (matches current `record.py` behavior).
- Three-stage migration: A=alias, B=migrate callers, C=deprecate `record` from banner.
- `abandoned` status: write the step_events row with `status=abandoned` and credit token usage. Phase aggregation in `phase_report` view filters on `status IN ('completed','recovered')` if needed; for now the `phase_events` write only triggers on `completed`, so abandoned steps never close a phase.
- `recovered` salvage path: reconstruction is the driver's responsibility (driver supplies `outputs` and `usage` from JSONL or git). `done` accepts the salvaged payload and writes it. If the driver cannot supply payload, it must use `abandoned` instead. `done` does not perform reconstruction itself.
- Phase 5 boundary: `ingest-feature-metrics` and `feature_metrics` table are NOT modified in Phase 4. Phase 5 absorbs that step.
