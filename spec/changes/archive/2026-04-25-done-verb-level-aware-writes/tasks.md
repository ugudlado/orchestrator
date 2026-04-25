# Tasks: Done verb + level-aware writes (Phase 4)

<!--
  Sequencing: Stage A (additive) → Stage B (caller migration) → Stage C (deprecation).
  TDD: every implementation task (GREEN) has a preceding test task (RED).
  Verifier: `pytest config/scripts/orchestrator_next/tests/<test_file>.py -x` for unit tests;
  `bash scripts/m8-gates.sh` for the banner gate; `duckdb metrics.duckdb -c '...'` for schema.
-->

## Stage A — additive (alias + tables + boundary logic)

- [x] T-1: Write tests for migration `0003_phase_events_driver_sessions.sql` (RED). FR-3, AC-9 — new tables must be created idempotently and recorded in `schema_migrations`.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_migration_0003.py -x` runs and FAILS (file missing). Tests cover: (a) `phase_events` table created with expected columns; (b) `driver_sessions` table created with expected columns; (c) re-running `_run_migrations` is a no-op; (d) `0003_phase_events_driver_sessions.sql` recorded in `schema_migrations` exactly once.

- [x] T-2: Implement migration `0003_phase_events_driver_sessions.sql` (GREEN). FR-3.
  Verify: T-1 tests pass. `duckdb $TMPDIR/test.duckdb -c "DESCRIBE phase_events"` returns the column list specified in design.md § Components 3. Same for `driver_sessions`.
  depends: T-1

- [x] T-3: Write tests for `_detect_boundary` pure function (RED). FR-4 — boundary detection logic is small, branchy, and critical; needs 100% coverage.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_boundary_detection.py -x` runs and FAILS. Tests cover: (a) NONE when status != completed; (b) NONE when step_id is not last in phase active; (c) PHASE when step_id is last but phase is not last in workflow_plan keys; (d) FEATURE when step_id is last AND phase is last; (e) NONE when workflow_plan has empty phase block.
  depends: T-2

- [x] T-4: Implement `_detect_boundary` in `record.py` (GREEN). FR-4.
  Verify: T-3 tests pass. Function is importable as `from orchestrator_next.record import _detect_boundary`.
  depends: T-3

- [x] T-5: Write tests for `_write_phase_event` aggregate insert (RED). FR-5 — phase_events row must be aggregated from step_events for the (repo_root, change_id, phase).
  Verify: `pytest config/scripts/orchestrator_next/tests/test_phase_boundary_write.py::test_write_phase_event -x` runs and FAILS. Test seeds 3 `step_events` rows for a phase, calls `_write_phase_event`, asserts one `phase_events` row exists with summed cost_usd / token columns / step_count = 3.
  depends: T-2

- [x] T-6: Implement `_write_phase_event` helper in `record.py` (GREEN). FR-5.
  Verify: T-5 tests pass.
  depends: T-5

- [x] T-7: Write tests for `_resolve_driver_session` lifted from `bin/orchestrator` (RED). FR-6 — session-id resolution must reproduce the legacy `_ingest_driver_main` behavior.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_boundary_write.py::test_resolve_driver_session -x` FAILS. Tests cover: (a) env var `$ORCHESTRATOR_DRIVER_SESSION_ID` honored; (b) JSONL fallback finds the most recent file by mtime; (c) raises when neither resolves; (d) returned `cost_usd` matches `_compute_cost_usd` over the JSONL token totals.
  depends: T-2

- [x] T-8: Implement `_resolve_driver_session` + `_write_driver_session` in `record.py` (GREEN). FR-6, FR-8 (absorbs ingest-driver logic).
  Verify: T-7 tests pass.
  depends: T-7

- [x] T-8a: Write tests for `_resolve_subagent_rows` + `_write_subagent_events` (RED). FR-6a, AC-6a — `_ingest_subagents_main` absorption must reproduce per-subagent synthetic step_events rows so the `agent_report` view continues to attribute usage by subagent.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_subagent_absorption.py -x` FAILS. Tests cover: (a) `_resolve_subagent_rows` returns one tuple per discovered subagent JSONL with `agent_name` from `agent-<id>.meta.json` `agentType` (fallback `subagent-unknown` when meta missing); (b) malformed meta.json or unreadable JSONL on one subagent skips that row (stderr log) without raising; (c) `_resolve_subagent_rows` does NOT open a DuckDB connection or BEGIN — it is pure parsing; (d) `_write_subagent_events` calls `upsert_synthetic_event` once per row with `phase='meta'`, `step_id='subagent-<agent_id>'`, and `agent_name` from the resolve step; (e) idempotency: if a `step_events` row already exists for `(repo_root, change_id, phase='meta', step_id, attempt=1)` with non-zero `input_tokens`, the insert is skipped; (f) `cost_usd` matches `_compute_cost_usd` over the JSONL token totals; (g) `agent_report` view returns the inserted rows grouped by `agent_name` after a feature-boundary call.
  depends: T-2

- [x] T-8b: Implement `_resolve_subagent_rows` + `_write_subagent_events` in `record.py` (GREEN). FR-6a, FR-8 (absorbs ingest-subagents logic).
  Verify: T-8a tests pass. `python -c "from orchestrator_next.record import _resolve_subagent_rows, _write_subagent_events"` succeeds. Logic mirrors `bin/orchestrator:_ingest_subagents_main` (lines 140-265) — uses `discover_subagents`, `extract_agent_usage`, `locate_subagent_jsonl_path` from `orchestrator_next.jsonl_usage` and `upsert_synthetic_event` from `orchestrator_next.upsert`.
  depends: T-8a

- [x] T-9: Write tests for `record()` status dispatch (RED). FR-2 — completed/recovered/abandoned must each produce the documented behavior.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_done_status_dispatch.py -x` FAILS. Tests cover: (a) `status: completed` → step_events row written normally; (b) `status: recovered` → step_events row with status=recovered, no boundary check even on last-step payload; (c) `status: abandoned` → state.yaml.status set to "blocked", step_events row with status=abandoned; (d) missing status defaults to "completed"; (e) invalid status returns exit 3.
  depends: T-4

- [x] T-10: Implement `payload.status` dispatch in `record()` (GREEN). FR-2.
  Verify: T-9 tests pass.
  depends: T-9, T-6

- [x] T-11: Write tests for atomic boundary write + ROLLBACK (RED). FR-5, FR-6, FR-6a, NFR-2, NFR-3, AC-2, AC-6, AC-6a, AC-7 — atomicity is critical.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_phase_boundary_write.py::test_atomic_commit -x` and `::test_rollback_on_failure -x` FAIL. Tests cover: (a) phase boundary call → step+phase rows committed in same transaction; (b) feature boundary call → step+phase+driver_session+per-subagent step_events rows all committed in same transaction; (c) `_write_phase_event` mocked to raise → no step_events row (driver or subagent) remains in DB after the call (ROLLBACK verified by SELECT COUNT); (d) exit code is non-zero on boundary write failure; (e) non-boundary failure stays fail-soft (exit 0); (f) `_resolve_subagent_rows` and `_write_subagent_events` are invoked in the FEATURE-boundary path with subagent JSONL parsing happening BEFORE the BEGIN (verified by mock ordering).
  depends: T-6, T-8, T-8b

- [x] T-12: Implement BEGIN/COMMIT/ROLLBACK wiring in `record()` (GREEN). FR-5, FR-6, FR-6a, NFR-2, NFR-3.
  Verify: T-11 tests pass. Manual `duckdb $TMPDIR/test.duckdb -c "SELECT * FROM phase_events"` after a passing test shows the expected row. `duckdb $TMPDIR/test.duckdb -c "SELECT agent_name, COUNT(*) FROM step_events WHERE phase='meta' GROUP BY agent_name"` shows one row per discovered subagent.
  depends: T-11, T-10

- [x] T-13: Write tests for `done` verb dispatch via `bin/orchestrator` (RED). FR-1 — both verbs must route to record_main during alias period.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_done_verb_alias.py -x` FAILS. Tests cover: (a) `bin/orchestrator done state.yaml <<<{}` invokes record_main; (b) `bin/orchestrator record state.yaml <<<{}` continues to invoke record_main (compat); (c) usage banner with no args mentions both verbs in Stage A.

- [x] T-14: Add `done` to verb dispatch in `bin/orchestrator` (GREEN). FR-1, NFR-1 (bootstrap safety).
  Verify: T-13 tests pass. `bin/orchestrator done` and `bin/orchestrator record` both produce identical output for a fixed input payload (golden file diff = 0).
  depends: T-13

- [x] T-15: Update `m8-gates.sh:45` to accept either verb (Stage A interim). FR-10 — gate must not regress while Stage B caller migration is pending.
  Verify: `bash scripts/m8-gates.sh` exits 0. Inspect script: line 45 grep accepts `done\|record`.
  depends: T-14

- [x] T-16: Stage A review checkpoint (phase gate).
  Verify: All Stage A unit tests pass; `bash scripts/m8-gates.sh` passes; `pytest config/scripts/orchestrator_next/tests/ -x` passes overall (existing suite plus the new tests); coverage >= 90% on changed files in `record.py`.

## Stage B — caller migration

- [x] T-17: Write tests for `test_prose_contracts.py` updated assertions for `done` (RED). FR-11 — the prose-contract test must drive the prose updates so we don't drift.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_prose_contracts.py::test_fr6_agents_forbid_state_edits -x` FAILS because agents/* still say `record`.
  depends: T-16

- [x] T-18: Migrate `agents/developer.md:213` and `agents/workflow-init.md:90` from `record` to `done` (GREEN). FR-9.
  Verify: `grep -n "orchestrator record" agents/developer.md agents/workflow-init.md` returns no matches; T-17 tests pass.
  depends: T-17

- [x] T-19: Migrate `skills/orchestrate/SKILL.md` (lines 88, 135, 139, 174 plus any additional `orchestrator record` matches discovered by grep) from `record` to `done`. FR-9 — SKILL.md is the orchestrate skill's dispatch loop; this is the highest-impact prose change.
  Verify: `grep -n "orchestrator record" skills/orchestrate/SKILL.md` returns no matches. Manual smoke: re-run a feature workflow's specify phase under `--auto` against a throwaway fixture; step_history advances correctly.
  depends: T-17

- [x] T-20: Migrate `CLAUDE.md:33` from `record` to `done`. FR-9 — global repo mandate.
  Verify: `grep -n "orchestrator record" CLAUDE.md` returns no matches.
  depends: T-17

- [x] T-21: Migrate `config/steps/ingest-feature-metrics.yaml:27` instruction prose from `record` to `done`. FR-9.
  Verify: `grep -n "orchestrator record" config/steps/ingest-feature-metrics.yaml` returns no matches.
  depends: T-17

- [x] T-22: Tighten `m8-gates.sh:45` to assert `done` strict + `record` absent from banner. FR-10 — TDD-red intent: this task tightens the gate ONLY; the banner update (T-25 in Stage C) makes the gate green. Between T-22 and T-25 the gate is intentionally red so the banner change is gate-driven.
  Verify: After this task only, `bash scripts/m8-gates.sh` exits non-zero with a "banner still mentions `record`" message; the gate script itself contains the strict assertion (`grep -c "orchestrator done" bin/orchestrator >= 1` and `grep -c "orchestrator record" bin/orchestrator == 0`). Inspect: line 45 implements that strict pair.
  depends: T-19, T-20

- [x] T-23: Stage B review checkpoint (phase gate).
  Verify: All `grep -rn "orchestrator record" agents/ skills/ CLAUDE.md config/steps/` checks return zero hits. `pytest config/scripts/orchestrator_next/tests/test_prose_contracts.py -x` passes. Stage A tests still pass. Both `record` and `done` verbs continue to dispatch identically (re-run T-14 acceptance). `m8-gates.sh` is intentionally red until T-25 — this checkpoint does NOT assert gate exit 0.

## Stage C — deprecation

- [x] T-24: Write tests for `bin/orchestrator` Stage C banner + verb tuple (RED). FR-12 — `record` must be removed from banner but still routable; ingest verbs must be gone.
  Verify: New tests in `test_done_verb_alias.py::test_stage_c_banner` and `::test_record_silent_routing` and `::test_ingest_verbs_removed` FAIL. Tests cover: (a) usage banner does not contain "ingest-driver" or "ingest-subagents" or "orchestrator record"; (b) `bin/orchestrator record state.yaml <<<{}` still routes correctly; (c) `bin/orchestrator ingest-driver` returns non-zero with "unknown verb"-style message.
  depends: T-23

- [x] T-25: Update `bin/orchestrator` usage banner (line 44) and accepted-verb tuple (line 334); remove `_ingest_driver_main` and `_ingest_subagents_main` functions (the `_compute_cost_usd` import inside those functions is removed along with them; the function itself stays in `record.py` and is now consumed by `_resolve_driver_session`) (GREEN). FR-8, FR-12.
  Verify: T-24 tests pass. `grep -n "_ingest_driver_main\|_ingest_subagents_main" bin/orchestrator` returns nothing. `python -c "from orchestrator_next.record import _compute_cost_usd"` still succeeds. `bash scripts/m8-gates.sh` now exits 0 (the T-22 strict gate goes green once banner no longer mentions `record`).
  depends: T-24

- [x] T-26: Remove `ingest-driver-auto` and `ingest-subagents-auto` from `config/workflows/_complete-phase.yaml`; delete `config/steps/ingest-driver-auto.yaml`, `config/steps/ingest-subagents-auto.yaml`, `scripts/inline/ingest-driver-auto.py`, `scripts/inline/ingest-subagents-auto.py`. FR-7 — inline ingest steps are obsolete now that boundary writes happen inside `done`.
  Verify: `grep -rn "ingest-driver-auto\|ingest-subagents-auto" config/ scripts/` returns no matches. `ls config/steps/ingest-*-auto.yaml scripts/inline/ingest-*-auto.py` shows no such files.
  depends: T-25

- [x] T-27: End-to-end smoke — run `_complete-phase` against an archived feature fixture. AC-2, AC-6, AC-9, AC-10 — the complete phase must still finish and now produce `phase_events` and `driver_sessions` rows.
  Verify: After running the complete phase against the fixture, `duckdb metrics.duckdb -c "SELECT COUNT(*) FROM phase_events WHERE change_id = '<fixture_id>'"` returns >= 3 (one per phase) and `SELECT COUNT(*) FROM driver_sessions WHERE change_id = '<fixture_id>'` returns 1.
  depends: T-26

- [x] T-28: SQL field-name validation against live schema. design.md § Decisions explicitly commits to verifying column names against the live DB after migration runs (cycle-12 step contract rule).
  Verify: `duckdb $METRICS_DB -c "DESCRIBE phase_events"` column list matches design.md spec exactly. Same for `driver_sessions`. No drift.
  depends: T-2

- [x] T-29: Stage C review checkpoint (phase gate).
  Verify: All Stage A + B + C tests pass. `bash scripts/m8-gates.sh` passes (strict-`done` mode green again per T-25). `pytest config/scripts/orchestrator_next/tests/ -x` passes. Manual: run `bin/orchestrator` with no args; banner shows `done` only and is free of `record`/`ingest-driver`/`ingest-subagents`. Coverage >= 90% on changed files.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- Format: per artifact-formats.md § Task Format Contract -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->

## Fix Tasks (from run-phase-review — specify phase)

- [x] FT-1: Resolved via Option A (absorb subagent logic). spec.md adds FR-6a + AC-6a; design.md adds `_resolve_subagent_rows` + `_write_subagent_events` to Low-Level Design, Decisions, Trade-offs; tasks.md adds T-8a/T-8b and extends T-11/T-12. Helper layout: parsing OUTSIDE the BEGIN/COMMIT block, inserts INSIDE, fail-soft per row.
  Verify: `grep "_ingest_subagents_main\|subagent" spec.md design.md` shows consistent treatment — both describe absorption with design.md providing the implementation. No artifact says "absorbs" while the other says "deleted".

- [x] FT-2: Resolved by approach (b) — T-22 verify clause now states the gate is intentionally red until T-25 makes the banner clean. T-23 explicitly notes the gate is red at this checkpoint and does NOT assert exit 0. T-25 verify now also asserts the gate transitions to exit 0 after the banner update.
  Verify: T-22 verify says gate exits non-zero; T-23 verify says gate is intentionally red; T-25 verify says gate exits 0. No task verify clause claims `m8-gates.sh exits 0` before the banner has been updated.

- [x] FT-3: Resolved by reformatting all task entries to Task Format Contract: `- [ ] T-N: description`, plain `  Verify:` indented 2 spaces, separate `  depends: T-N` line. Rationale embedded in the description text rather than `**Why**:` labels.
  Verify: `grep -P "^- \[[ x]\] T-\d+:" tasks.md | wc -l` equals total task count (29 + 3 FT). `grep "\*\*Verify\*\*\|\*\*Why\*\*" tasks.md` returns no matches.
