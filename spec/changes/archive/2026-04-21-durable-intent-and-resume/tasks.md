# Tasks — Durable intent + idempotent resume

- [x] T-1: Write test for `upsert_pending_step_event` covering (a) single-row insert with NULL cost/usage, (b) idempotent re-insert replaces without duplicating, (c) slug-guard rejection.
  Why: FR-1, AC-1, NFR-3, NFR-4
  Files: config/scripts/orchestrator_next/tests/test_upsert_pending.py
  Approach: use the `in_memory_db` pattern from test_record_cost_compute.py:108-113; import the new helper; assert row shape and PK behaviour.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_upsert_pending.py -q` fails (function not yet implemented).

- [x] T-2: Implement `upsert_pending_step_event` helper in upsert.py.
  Why: FR-1
  Files: config/scripts/orchestrator_next/upsert.py
  Approach: add the keyword-only-arg function defined in design.md § Pseudocode; reuse `_INSERT_OR_REPLACE`; NULL all usage/cost/tool_calls columns; apply `_SLUG_RE` guard; no tool_calls fan-out.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_upsert_pending.py -q` passes; all prior upsert tests still pass.
  depends: T-1

- [x] T-3: Write tests for `reconcile_in_progress` covering (a) yaml orphan stripped when DB empty, (b) DB row materialised into yaml when yaml lacks it, (c) matching (phase, step_id, attempt) on both sides is preserved unchanged, (d) non-in_progress history entries are never touched.
  Why: FR-4, FR-5, AC-4, AC-5, NFR-4
  Files: config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py
  Approach: `in_memory_db` + synthesised `State` fixtures; directly invoke `reconcile_in_progress(state, db, context)` and assert `state.step_history` mutations.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py -q` fails (module not yet created).
  depends: T-2

- [x] T-4: Implement `reconcile.py` module with `reconcile_in_progress(state, db, context)`.
  Why: FR-4, FR-5
  Files: config/scripts/orchestrator_next/reconcile.py
  Approach: one parameterised SELECT filtered by `status='in_progress'`; set difference on `(phase, step_id, attempt)` tuples; mutate `state.step_history` in place per design.md § Pseudocode; no disk writes.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py -q` passes.
  depends: T-3

- [x] T-5: Audit ALL existing `retry_step` references (per `rg -n retry_step` against HEAD: `dispatch.py:293`, `skills/orchestrate/SKILL.md:145`, `test_dispatch_allowed_tools.py:208-220`, `test_orchestrator_next.py:124-125`, `test_attempt_counting.py:6, 74-78`, `test_cost_so_far.py:112, 117`, `golden/state-in-progress-no-ended.json:2`). Write tests for the new `resume_step` dispatch branch covering (a) last entry `in_progress` → returns `action='resume_step'` with `is_resume: true` and ORIGINAL `attempt` (unchanged, not max+1), (b) original `started_at` preserved in action, (c) contract inputs/env/step_context populated identically to `run_step`. Retarget each existing test above: update `test_dispatch_allowed_tools.py:208-220`, `test_orchestrator_next.py:124-125`, `test_cost_so_far.py:112, 117` to assert `resume_step`; update `golden/state-in-progress-no-ended.json:2` to `"resume_step"`. IMPORTANT: `test_attempt_counting.py` fixture (`state-crash-midstep.yaml`) has `[1 completed, 2 failed, 2 in_progress]` and today asserts `attempt=3` — under Phase 2 semantics, resume keeps the in_progress entry's attempt=2 unchanged. Update the test's `self.assertEqual(actual.get("attempt"), 3, ...)` → `2`, assert `action='resume_step'`, and rewrite the docstring+comment block to reflect resume semantics. Also drop the `previous_failure` assertion (not present on `resume_step`).
  Why: FR-3, FR-10, AC-2, R-3, cycle-16 caller-site-verification rule
  Files: config/scripts/orchestrator_next/tests/test_dispatch_resume.py, config/scripts/orchestrator_next/tests/test_dispatch_allowed_tools.py, config/scripts/tests/test_orchestrator_next.py, config/scripts/tests/test_attempt_counting.py, config/scripts/tests/test_cost_so_far.py, config/scripts/tests/golden/state-in-progress-no-ended.json
  Approach: hand-built `State` fixture with one `in_progress` entry (attempt=1) for the new file; retarget the listed existing tests in-place without changing fixture YAMLs.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_dispatch_resume.py config/scripts/orchestrator_next/tests/test_dispatch_allowed_tools.py config/scripts/tests/test_orchestrator_next.py config/scripts/tests/test_attempt_counting.py config/scripts/tests/test_cost_so_far.py -q` — resume tests fail (branch not yet replaced); retargeted tests also fail against current `retry_step` production. Both sets go GREEN after T-6.
  depends: T-4

- [x] T-6: Replace `retry_step` branch in dispatch.py (lines 270-308) with `resume_step` branch per design.md § Pseudocode. Use `last.attempt if last.attempt is not None else 1` directly — do NOT call `_compute_attempt` on this branch. Update the module docstring (line 7) to list `resume_step` and remove `retry_step`.
  Why: FR-3, FR-10, OQ-1 resolution
  Files: config/scripts/orchestrator_next/dispatch.py
  Approach: delete lines 270-308 body; paste the resume_step action builder from design.md; keep everything else in `dispatch()` unchanged.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_dispatch_resume.py config/scripts/orchestrator_next/tests/test_dispatch_allowed_tools.py -q` passes; full dispatch test module passes.
  depends: T-5

- [x] T-7: Update `config/steps/contracts/step-dispatch.md` (replace `retry_step` section at lines 78-96 with a `resume_step` section; update exit-code table at line 24) and `skills/orchestrate/SKILL.md` (replace `retry_step` handler at line 145 with a `resume_step` handler that logs `RESUMING step <id> (attempt <N>)` on stderr even under `flags.auto = true`, then executes identically to `run_step`/`run_inline`).
  Why: FR-10, AC-9
  Files: config/steps/contracts/step-dispatch.md, skills/orchestrate/SKILL.md
  Approach: prose + JSON example edits; mirror the new action shape from design.md.
  Verify: `rg -n "retry_step" config/steps/contracts/step-dispatch.md skills/orchestrate/SKILL.md` returns no matches; `rg -n "resume_step" ...` finds the new sections.
  depends: T-6

- [x] T-8: Write tests for `bin/orchestrator` post-dispatch pending write + reconcile integration covering (a) after `next` returns `run_step`/`run_inline`, an `in_progress` row exists in DB AND a matching state.yaml entry exists, (b) `verify_phase`/`complete_workflow`/`blocked` actions do NOT write a pending row, (c) `attempt=2` in_progress coexists with `attempt=1` terminal row.
  Why: FR-1, FR-2, FR-8, FR-9, AC-1, AC-6, AC-7
  Files: config/scripts/orchestrator_next/tests/test_dispatch_pending_row.py
  Approach: invoke `bin/orchestrator next <state.yaml>` as a subprocess with `METRICS_DB=<tmp-path>.duckdb`; after, query the DB directly and parse state.yaml to assert the row/entry. Use the existing `ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE` monkeypatch pattern.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_dispatch_pending_row.py -q` fails (pending write not yet wired).
  depends: T-4

- [x] T-9: Restructure the `_metrics_db_path` block in `bin/orchestrator` so `_db` stays open across `dispatch()`. Move the existing `_db.close()` (today at line 580, before dispatch at 585) to AFTER the new post-dispatch pending-write block. Add: (a) reconcile call — after `ensure_schema(_db)` and the terminal upsert loop, before `dispatch()`, call `reconcile_in_progress(state, _db, _context)` in a try/except; (b) post-dispatch pending write — if `action.get("action") in {"run_step", "run_inline", "resume_step"}` and `_db` is not None, call `upsert_pending_step_event` with action fields and mirror-append to state.yaml via a new `_append_in_progress_state_entry_if_absent` helper defined inline in `bin/orchestrator` (copy the pre-write-bytes corruption-guard pattern from record.py:399-414). Ensure `_db.close()` fires in both the dispatch-exception path and the happy path (single close at end of the block). Full pseudocode in design.md § `bin/orchestrator` — reconcile + post-dispatch pending write.
  Why: FR-1, FR-2, FR-8, OQ-2, OQ-4
  Files: bin/orchestrator
  Approach: replace the existing DB-lifecycle region (bin/orchestrator:554-595) with the restructured version from design.md; no changes outside that region; reconcile and pending write are verb-gated and try/except-wrapped so neither blocks dispatch.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_dispatch_pending_row.py -q` passes; existing `test_cost_so_far.py` and `test_orchestrator_next.py` still pass; `bin/orchestrator next` on a valid state.yaml exits with the expected code; no "Connection already closed" stderr warnings.
  depends: T-6, T-8

- [x] T-10: Write tests for `record()` cleaning the pending row + state.yaml entry, covering (a) after terminal `completed` record, the matching in_progress row is gone from DB, (b) the in_progress entry is gone from state.yaml.step_history (only the new terminal entry remains for this (step_id, phase, attempt)), (c) `sum_cost_usd` returns the same value whether in_progress rows existed before the record or not, (d) record with `db=None` (offline) still scrubs state.yaml (no crash).
  Why: FR-6, FR-7, AC-3, AC-8, NFR-5
  Files: config/scripts/orchestrator_next/tests/test_record_cleans_pending.py
  Approach: `in_memory_db` + prepared state.yaml containing an in_progress entry + a matching DB row; invoke `record()` directly; assert both stores are clean post-call.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_record_cleans_pending.py -q` fails (record not yet updated).
  depends: T-4

- [x] T-11: Update `record()` in record.py to (a) filter out the in_progress entry for `(step_id, phase)` from history before the `history.append(entry)` at line 489, (b) after the state.yaml write at line 497-498, issue the parameterised DELETE (per design.md § record() — pending DELETE) if `db is not None`. DELETE failure must not block record success — wrap in try/except and log to stderr.
  Why: FR-6, FR-7
  Files: config/scripts/orchestrator_next/record.py
  Approach: add the list-comprehension filter before line 489; add the DELETE block after the state.yaml write with a try/except matching the existing `[record]` stderr warning style.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_record_cleans_pending.py config/scripts/orchestrator_next/tests/test_record_cost_compute.py -q` passes.
  depends: T-10

- [x] T-12: Two-cycle invariant test: run `next`→`record`→`next`→`record` for two different steps in the same phase; assert DuckDB `step_events` has zero rows with `status='in_progress'` at the end.
  Why: FR-6, FR-7, NFR-5, AC-10
  Files: config/scripts/orchestrator_next/tests/test_record_cleans_pending.py
  Approach: extend the test file from T-10 with a method that drives two full cycles against a shared DB + state.yaml, then `SELECT COUNT(*) FROM step_events WHERE status='in_progress'` and assert zero.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_record_cleans_pending.py::test_two_cycle_lifecycle_leaves_no_in_progress_rows -q` passes.
  depends: T-11

- [x] T-13: Driver integration test: `resume_step` action triggers `RESUMING step` stderr log even under `flags.auto = true`.
  Why: FR-10, AC-9
  Files: config/scripts/orchestrator_next/tests/test_dispatch_resume.py
  Approach: extend the T-5 file with a subprocess invocation that exercises the `/orchestrate` skill path (or directly asserts SKILL.md's stderr emission if the skill is unit-testable via its inline script); capture stderr; assert `"RESUMING step" in captured.stderr`. If the skill is not directly unit-testable, mock the code path that writes the log and assert the write occurs when `is_resume=True`.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_dispatch_resume.py::test_resume_emits_stderr_log_in_auto_mode -q` passes.
  depends: T-7, T-9

- [x] T-14: End-to-end test of the full crash-and-resume cycle: run `orchestrator next` (writes in_progress row + yaml entry), simulate a crash by NOT calling record, then run `orchestrator next` again and assert the action is `resume_step` with `is_resume: true` and the SAME `attempt`. Then run `orchestrator record` with a terminal status and assert the in_progress row is gone from both stores.
  Why: FR-1, FR-3, FR-6, FR-7, NFR-2, AC-1, AC-2, AC-3
  Files: config/scripts/orchestrator_next/tests/test_dispatch_resume.py
  Approach: append a new test method to the file from T-5; use subprocess invocations with a shared `METRICS_DB` path so the DB persists across calls; assert on both DB queries and state.yaml contents at each step.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_dispatch_resume.py::test_full_crash_and_resume_cycle -q` passes.
  depends: T-9, T-11
