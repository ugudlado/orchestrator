---
feature-id: done-verb-level-aware-writes
linear-ticket: N/A
---

# Discovery Brief: done-verb-level-aware-writes

## Feature Summary

Phase 4 of the `workflow-engine-as-state-machine` refactor. The goal is to make `orchestrator record` the single write path for all metrics — step-level, phase-level, and feature-level — by: (1) renaming the verb from `record` to `done`, (2) adding `payload.status` dispatch so `done` handles `completed`, `recovered`, and `abandoned` outcomes, and (3) adding level-aware write logic that detects phase and feature boundaries and atomically writes to new `phase_events` and `driver_sessions` tables. As a consequence of level-aware writes, the `ingest-driver-auto` and `ingest-subagents-auto` inline steps in the complete phase can be removed — their work is absorbed into `done`. Phases 2 and 3 explicitly deferred this work here.

## What I Understand

The underlying goal is consolidation: today's metrics pipeline has three separate CLI verbs (`record`, `ingest-driver`, `ingest-subagents`) that each write to DuckDB at different times. This creates a sequential dependency chain in the complete phase that is fragile — if `ingest-driver-auto` or `ingest-subagents-auto` skips (fail-soft by design), metrics are silently missing. The rename to `done` is partly semantic (clearer intent) but the real value is the level-aware write: when `done` observes the last step of a phase, it writes a `phase_events` row atomically in the same transaction as the `step_events` row. No separate step, no silent skip.

The secondary goal is eliminating the cognitive overhead for orchestrator drivers: instead of remembering to call three different verbs at different points, drivers call `done` once per step and the engine handles boundary detection internally.

## What Already Exists

### Codebase

**`orchestrator record` — the current write path**

`/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/config/scripts/orchestrator_next/record.py`
- `main()` at line 613: the `orchestrator record` entry point. Reads JSON payload from stdin, calls `record()`.
- `record()`: writes step to `state.yaml.step_history`, upserts `step_events` in DuckDB.
- `_compute_cost_usd()`: utility function, imported by `bin/orchestrator` for the ingest commands.

`/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/bin/orchestrator` (lines 334, 352)
- Verb dispatch at line 334: recognized verbs are `next`, `record`, `doctor`, `ingest-driver`, `ingest-subagents`.
- `from orchestrator_next.record import main as record_main` at line 352.
- `_ingest_driver_main()` (lines 53–137): imports `from orchestrator_next.record import _compute_cost_usd` at line 84.
- `_ingest_subagents_main()` (lines 140–265): imports `from orchestrator_next.record import _compute_cost_usd` at line 170.

**Complete caller inventory for `orchestrator record`**

Production callers (must be migrated to `orchestrator done`):
- `skills/orchestrate/SKILL.md:135` — inline script dispatch path
- `skills/orchestrate/SKILL.md:139` — legacy inline-instruction path
- `skills/orchestrate/SKILL.md:174` — agent spawn path (also covers resume_step)
- `agents/developer.md:213` — mandate prose: "State updates MUST use `orchestrator record`"
- `agents/workflow-init.md:90` — same mandate
- `CLAUDE.md:33` — global repo mandate for all spawns
- `config/steps/ingest-feature-metrics.yaml:27` — instruction prose

Hard-coded string assertions (will break without explicit update):
- `scripts/m8-gates.sh:45`: `grep -q "orchestrator record"` against usage banner — WILL FAIL if banner changes
- `config/scripts/orchestrator_next/tests/test_prose_contracts.py:135–136`: `assert "orchestrator record" in content` for `developer.md` and `workflow-init.md` — WILL FAIL after agent migration

Python import callers of `orchestrator_next.record` (all must remain importable or be updated):
- `bin/orchestrator:84,170,352`
- `config/scripts/orchestrator_next/tests/test_record_validation.py:18`
- `config/scripts/orchestrator_next/tests/test_repeat_until.py:25`
- `config/scripts/orchestrator_next/tests/test_record_cost_compute.py:28`
- `config/scripts/orchestrator_next/tests/test_dispatch_resume.py:798`
- `config/scripts/orchestrator_next/tests/test_pricing_lookup.py:28`
- `config/scripts/orchestrator_next/tests/test_record_cleans_pending.py:30`

**Complete caller inventory for `orchestrator ingest-driver`**

- `bin/orchestrator:53–137` — `_ingest_driver_main()` implementation
- `scripts/inline/ingest-driver-auto.py:100` — subprocess call
- `config/steps/ingest-driver-auto.yaml` — step contract (agent: inline, run: scripts/inline/ingest-driver-auto.py)
- `config/workflows/_complete-phase.yaml:20` — listed in complete phase step sequence

**Complete caller inventory for `orchestrator ingest-subagents`**

- `bin/orchestrator:140–265` — `_ingest_subagents_main()` implementation
- `scripts/inline/ingest-subagents-auto.py:100` — subprocess call
- `config/steps/ingest-subagents-auto.yaml` — step contract (agent: inline, run: scripts/inline/ingest-subagents-auto.py)
- `config/workflows/_complete-phase.yaml:21` — listed in complete phase step sequence

**Existing DuckDB tables (via `upsert.py`)**

- `step_events` — exists, written by `record.py`
- `tool_calls` — exists
- `feature_complexity` — exists
- `feature_metrics` — exists (DDL at `upsert.py:103–138`; written by separate `ingest-feature-metrics` step, not by `record`)
- `phase_events` — DOES NOT EXIST anywhere in the codebase; must be created in Phase 4
- `driver_sessions` — DOES NOT EXIST anywhere in the codebase; must be created in Phase 4

**Existing migrations**

- `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql`
- `config/scripts/orchestrator_next/migrations/0002_report_views.sql`

Phase 4 requires a new migration (likely `0003_phase_events_driver_sessions.sql`) for DDL of the two new tables.

**Phase 3 left intact** (per `spec/changes/archive/2026-04-24-report-views-retire-cli/spec.md`):
- `ingest-driver-auto` and `ingest-subagents-auto` steps in the complete phase — still present, not changed
- `ingest-feature-metrics` — still a separate step, explicit Phase 5 scope

### External

No external libraries needed. DuckDB, Python 3, and the existing `orchestrator_next` package cover the implementation surface. No standard ecosystem solution exists for this workflow-engine-specific concern.

## Build or Reuse?

**Reuse (extend existing `record.py`)**. The `record()` function and `main()` already handle the full state.yaml + DuckDB write cycle. Phase 4 extends this function with: (a) a new `status` field router for `completed`/`recovered`/`abandoned`, (b) boundary detection logic using the workflow plan, and (c) atomic multi-table writes when a boundary is detected. The `_ingest_driver_main` and `_ingest_subagents_main` functions in `bin/orchestrator` become internal calls from `record.py` rather than separate CLI verbs. The module is renamed from `record.py` to `done.py` as part of the verb rename.

Building from scratch would duplicate all the state.yaml validation, DuckDB connection handling, and `ensure_schema()` wiring that already works.

## Approaches Considered

### Approach A: What the backlog describes (atomic rename + level-aware writes in one pass)

Rename `record` → `done` as a single commit, update all callers simultaneously, add `phase_events`/`driver_sessions` DDL, and implement boundary detection in the same PR.

- Pros: Clean — no intermediate state where two verbs coexist.
- Cons: The bootstrap hazard makes this dangerous. The workflow running this feature uses `orchestrator record` to advance its own steps. If the rename happens mid-workflow, `orchestrator record` stops working before all callers are migrated, and the workflow can no longer record its own progress. A single-pass rename requires all callers to be migrated in one atomic commit — any missed caller is a broken workflow.
- Effort: Large (high coordination cost, high breakage risk).

### Approach B: Staged alias migration (recommended)

Three sub-steps in order:
1. Add `done` as an alias for `record` in `bin/orchestrator` (both verbs work; `record` is primary). Add `phase_events`/`driver_sessions` DDL via migration `0003`. Implement level-aware write logic behind `payload.status`. Ship this; the workflow continues to use `orchestrator record` safely.
2. Migrate all callers (orchestrate SKILL.md, agents, CLAUDE.md, step contracts, scripts) from `record` to `done`. Update `m8-gates.sh` to assert `done` in banner. Update `test_prose_contracts.py`. Ship this; both verbs still work.
3. Deprecate `record` from the usage banner (keep routing it to `done` silently for 1 release cycle to avoid hard breakage on in-flight workflows). Ship this.

- Pros: No workflow can be broken mid-flight. Each sub-step is independently verifiable. The bootstrap hazard is neutralized because `record` stays live during the migration.
- Cons: Slightly more implementation surface (alias routing layer). The `record` verb lives longer than necessary.
- Effort: Medium (three small sequential commits vs. one risky large one).

### Approach C: Keep `record` permanently, add `done` as the new verb without aliasing

Leave `record` working forever. Add `done` as a separate, richer verb. Over time callers migrate at their own pace.

- Pros: No migration risk at all.
- Cons: Two verbs with overlapping semantics permanently. The backlog intent is consolidation, not coexistence. This approach contradicts the feature goal.
- Effort: Small (but doesn't complete the feature).

## Recommendation

Approach B (staged alias migration). The bootstrap hazard is real — this workflow uses `orchestrator record` to record its own steps. Approach A requires perfect atomic coordination across 10+ call sites. Approach B eliminates that risk with a clear three-step sequence that can be tested at each boundary. Approach C is explicitly not the goal.

## Personas

- **Orchestrator driver** — the LLM agent running the dispatch loop (orchestrate SKILL.md). Calls `orchestrator done` once per step. Wants a single verb that handles all outcome types.
- **Workflow maintainer** — engineer updating step contracts, agent .md files, or the SKILL.md dispatch loop. Needs the rename surfaced clearly so no call site is missed.
- **Metrics consumer** — analyst or dashboard reading DuckDB views. Wants `phase_events` and `driver_sessions` tables populated automatically per-feature without manual ingest steps.
- **In-flight workflow** — an active feature whose state.yaml was created before the migration. Must continue to advance without requiring manual intervention if `orchestrator record` still routes to `done`.

## Use Cases

**UC-1: Step completion — driver calls `orchestrator done` with `status: completed`**
Driver completes a step and calls `orchestrator done state.yaml <<< {"step_id": "implement-feature", "phase": "implement", "status": "completed", "outputs": {...}, "usage": {...}}`. The CLI: writes to `state.yaml.step_history`, upserts a `step_events` row. If this is the last step of the current phase, atomically writes a `phase_events` row in the same transaction. If this is the last step of the workflow, atomically writes a `feature_metrics` row update and a `driver_sessions` row. Returns exit 0. Driver advances to next step.

**UC-2: Step salvage — driver calls `orchestrator done` with `status: recovered`**
An agent step failed but the driver recovered partial output from the session JSONL or git stash. Driver calls `done` with `status: recovered` and the salvaged outputs. The CLI: writes a `step_events` row with `status: recovered` (distinct from `completed`), includes partial outputs. Phase-boundary logic is NOT triggered for `recovered` rows — the phase is only closed when its final step is `completed`. Returns exit 0.

**UC-3: Step abandon — driver calls `orchestrator done` with `status: abandoned`**
The driver gives up on a step after max retries. Driver calls `done` with `status: abandoned`. The CLI: writes a `step_events` row with `status: abandoned`, sets `state.yaml.status: blocked`. Returns exit 0. Downstream readers can filter out `abandoned` steps when computing phase metrics.

**UC-4: Phase boundary detected — `phase_events` row written atomically**
Driver calls `done` for the last step of the `implement` phase. `done` detects this is the phase boundary (by comparing `step_id` against `plan.yaml`'s step list for the current phase). Within the same DuckDB transaction: upserts the `step_events` row, then inserts a `phase_events` row with aggregated phase totals (token sum, cost sum, step count, duration). Returns exit 0 with a `phase_boundary: true` field in stdout JSON so the driver can log the event.

**UC-EN-1: `orchestrator record` called after rename**
In-flight workflow that was started before migration calls `orchestrator record`. The alias routing in `bin/orchestrator` routes it silently to `done`. State.yaml is updated. Driver sees no error. This is the bootstrap hazard mitigation — the verb continues to work during the migration window.

**UC-EN-2: `done` called for a step that is not the last in its phase**
Boundary detection reads `plan.yaml` and confirms the current `step_id` is not the last step of the phase. No `phase_events` row is written. Normal `step_events` upsert proceeds. Returns exit 0.

**UC-EN-3: DuckDB unavailable during boundary write**
`done` writes `step_events` successfully but `phase_events` write fails (DuckDB locked, schema mismatch, etc.). Current `record.py` pattern: DB failures are non-fatal (logged to stderr, step continues). Phase 4 must decide: should a `phase_events` write failure be fatal (consistency guarantee) or fail-soft (availability guarantee)? This is an open architectural question.

## Scope

**In Scope (Phase 4)**
- Add `done` as an alias for `record` in `bin/orchestrator` (both verbs recognized and routing to same `main()`)
- Rename `record.py` → `done.py` in `orchestrator_next` package; update all `from orchestrator_next.record import ...` internal imports
- Add `payload.status` dispatch in `done()`: `completed` (current behavior), `recovered` (write partial row, no boundary trigger), `abandoned` (write row, set blocked)
- Add DDL for `phase_events` and `driver_sessions` tables via migration `0003`
- Implement level-aware write in `done()`: detect phase boundary from `plan.yaml`, write `phase_events` atomically with `step_events` in one DuckDB transaction
- Implement driver-session boundary write in `done()`: detect feature completion boundary, write `driver_sessions` row (absorbing `ingest-driver-auto` logic)
- Absorb `ingest-subagents-auto` logic into the `done` boundary write path
- Remove `ingest-driver-auto` and `ingest-subagents-auto` as separate complete-phase steps (after boundary write is verified)
- Remove `orchestrator ingest-driver` and `orchestrator ingest-subagents` from `bin/orchestrator` usage banner (keep internal routing for 1 cycle if needed)
- Migrate all callers: `skills/orchestrate/SKILL.md`, `agents/developer.md`, `agents/workflow-init.md`, `CLAUDE.md`, `config/steps/ingest-feature-metrics.yaml`
- Update `scripts/m8-gates.sh` to assert `orchestrator done` in banner
- Update `test_prose_contracts.py` assertions from `record` to `done`

**Out of Scope (Phase 4)**
- Absorbing `ingest-feature-metrics` step — this writes `feature_metrics` from `tasks.md + git log + state.yaml` and is explicitly a Phase 5 concern. The backlog separates the two phases for this reason.
- Changes to `orchestrator next` or the `next.py` dispatch path
- Changes to `reconcile.py` or the `in_progress` row logic from Phase 2
- Changes to DuckDB views created in Phase 3 (`feature_report`, `phase_report`, etc.)
- Any frontend, dashboard, or reporting changes
- Schema changes outside `orchestrator_next`

## UI Direction

N/A — no frontend, no UI components. Tech stack is bash/zsh/yaml/duckdb/python.

## Key Decisions

- Selected approach: **Approach B — Staged alias migration** (A=add `done` alias, B=migrate callers, C=deprecate `record` from banner). Auto-selection: lowest-complexity goal-aligned approach (S=2 vs M=3 for Approach A; Approach C XS=1 is goal-disqualified — perpetuates dual verbs).
- Bootstrap hazard mitigation: `record` stays live throughout the migration. Stage C keeps `record` accepted as an undocumented verb for one cycle.
- OQ-1 (boundary detection mechanism): read `state.yaml.workflow_plan[phase].active` (already in memory). No `plan.yaml` reads, no `next.py` annotations.
- OQ-2 (boundary write fail policy): FATAL on failure. BEGIN/COMMIT/ROLLBACK around step + phase + (optional) driver_session writes. Step write stays fail-soft on non-boundary calls (preserves current behavior).
- OQ-3 (driver_sessions session_id resolution): lifted from `bin/orchestrator:_ingest_driver_main` into `_resolve_driver_session()` helper in `record.py`. Resolution order: `$ORCHESTRATOR_DRIVER_SESSION_ID` env var → most-recent JSONL by mtime under `$HOME/.claude/projects/<encoded_repo>/` → raise.
- OQ-4 (`abandoned` semantics): write `step_events` row with `status=abandoned` and credit token usage; set `state.yaml.status=blocked`; do NOT trigger boundary write (only `completed` last-step closes a phase). Downstream views can filter on status.
- OQ-5 (module rename vs verb-only): **verb-only**. Module name stays `record.py`. Reduces blast radius (6 test files + `bin/orchestrator:84,170` keep working unchanged). Cosmetic rename can be a future cleanup.
- OQ-6 (m8-gates formulation): Stage A interim gate accepts `done|record`. Stage C gate asserts `done` strict AND `record` absent from banner. Avoids trivial pass-through during interim.
- OQ-7 (Phase 5 boundary): Phase 4 does NOT touch `ingest-feature-metrics`, `feature_metrics` table, or `upsert_feature_metrics()`. Spec.md "Out of scope" is explicit.
- Salvage path (`status: recovered`): driver supplies the salvaged `outputs` and `usage` from JSONL or git evidence. `done` accepts and writes the row with `status=recovered`. No boundary trigger. If the driver cannot supply payload, it must use `abandoned` instead — `done` does not perform reconstruction itself.
- SQL field-name validation: a dedicated task (T-28) runs `DESCRIBE phase_events` / `DESCRIBE driver_sessions` against the live DB after migration, asserting the exact column list from design.md (per cycle-12 learned rule).
- Subagent absorption (FT-1 resolution, 2026-04-25): Option A selected — `_ingest_subagents_main` logic is absorbed into `_resolve_subagent_rows` + `_write_subagent_events` helpers, called at the feature boundary inside the same DuckDB transaction as step/phase/driver_session writes. JSONL discovery + parse run OUTSIDE BEGIN/COMMIT to keep the transaction window short; only `upsert_synthetic_event` calls run INSIDE. Per-subagent row construction is fail-soft (one bad transcript skips that row, never aborts the transaction). Option B (drop subagent rows) was rejected because it silently breaks the `agent_report` view's per-subagent attribution, contradicts the parent backlog scope ("Absorbs ingest-driver/ingest-subagents as internal code paths"), and leaves Phase 5 with nothing to delete cleanly.

## Open Questions

**OQ-1: Phase boundary detection — how?**
`done` must know whether the current step is the last in its phase. The plan.yaml contains the full step list per phase. Does `done` read `plan.yaml` directly (requires `plan.yaml` path in the payload or as a state.yaml field), or does `next.py` annotate the action JSON with `is_phase_boundary: true` so `done` doesn't need to read the file? The second approach is cleaner but requires `next.py` to compute it.

**OQ-2: Phase boundary write failure — fatal or fail-soft?**
If the `phase_events` DuckDB write fails (lock, schema mismatch), should `done` fail with a non-zero exit (forcing the driver to retry the step) or log and continue (availability, but silent data loss)? The existing `record.py` pattern is fail-soft for DB writes. Changing this for boundary writes is a consistency-vs-availability tradeoff.

**OQ-3: `driver_sessions` row — when exactly is it written?**
`ingest-driver-auto` currently resolves the session_id from `$TMPDIR` UUID or JSONL scan. If this logic moves inside `done`, it must run at the feature-completion boundary (last step of the workflow). The session_id resolution must still work inside `done`'s process context. Is `$TMPDIR` still the same UUID at that point, or does the workflow environment change between step spawns?

**OQ-4: `abandoned` status and downstream aggregation**
When a step is `abandoned`, should `phase_events` aggregation exclude it, include it as zero-contribution, or mark the phase as `incomplete`? Downstream dashboard queries (phase_report view) need a consistent signal.

**OQ-5: `ingest-feature-metrics` explicit Phase 5 boundary**
The backlog description for Phase 4 says "absorbs ingest-driver/ingest-subagents." Phase 5 says "remove ingest-feature-metrics as a separate step." Confirm: Phase 4 does NOT touch `ingest-feature-metrics` or its DuckDB write path (`feature_metrics` upsert in `upsert.py:393`). Architect should confirm this boundary explicitly in the spec so it doesn't drift mid-implementation.

**OQ-6: Module rename (`record.py` → `done.py`) vs. keeping the module name**
Renaming the Python module changes all `from orchestrator_next.record import ...` paths in 6 test files plus `bin/orchestrator`. Alternatively, keep `record.py` as the module name but rename only the CLI verb. This avoids churn in test imports but leaves a semantic mismatch (module called `record`, verb called `done`). Which is preferred?

**OQ-7: `scripts/m8-gates.sh` gate update timing**
The gate at line 45 asserts `orchestrator record` in the usage banner. This gate runs in phase review CI. If the gate is updated to assert `done` in step 3 of the staged migration (when `record` is deprecated from the banner), but the phase review runs after step 2 (callers migrated but banner still shows both), the gate will pass trivially during the intermediate state. Is a gate that asserts `done` AND `record` NOT present in the banner the right formulation for step 2?

## Technical Context

**Key files for implementation**
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/config/scripts/orchestrator_next/record.py` — extend here
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/config/scripts/orchestrator_next/upsert.py` — add DDL for `phase_events`, `driver_sessions`
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/bin/orchestrator` — add `done` verb, alias `record` → `done`
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/config/scripts/orchestrator_next/migrations/` — add `0003_phase_events_driver_sessions.sql`
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/skills/orchestrate/SKILL.md` — migrate 3 call sites + 2 prose references
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/scripts/m8-gates.sh:45` — update banner assertion
- `/Users/spidey/code/feature_worktrees/done-verb-level-aware-writes/config/scripts/orchestrator_next/tests/test_prose_contracts.py:135–136` — update string assertions

**Library versions**
- Python: 3.x (bash-3.2 workaround not needed — Python for all new logic, per project learning)
- DuckDB: version in use via `ensure_schema()` in `upsert.py`
- No new external dependencies needed

**Integration points**
- `orchestrator_next.upsert.ensure_schema()` — must be called before any `phase_events` or `driver_sessions` write
- `orchestrator_next.record._compute_cost_usd()` — referenced by `_ingest_driver_main` and `_ingest_subagents_main` at `bin/orchestrator:84,170`; if module is renamed, these imports must update too
- `plan.yaml` — boundary detection needs to read this file; path must be resolvable from `done`'s execution context

**Bootstrap hazard mitigation (critical)**
The workflow advancing this feature calls `orchestrator record` for its own steps. The alias must be added as the very first sub-step of Phase 4 implementation, before any caller migration. Order: (1) alias → (2) migrate callers → (3) deprecate `record` from banner. Do NOT invert this order.
