# Tasks: Subprocess-Per-Step Observability

- [ ] T-1 Write tests: `orchestrator next` dispatcher fixtures (RED)
  - **Files**: `config/scripts/tests/test_orchestrator_next.py`,
    `config/scripts/tests/fixtures/state-pending-inline.yaml`,
    `config/scripts/tests/fixtures/state-pending-runfield.yaml`,
    `config/scripts/tests/fixtures/state-in-progress-no-ended.yaml`,
    `config/scripts/tests/fixtures/state-phase-done-needs-verify.yaml`,
    `config/scripts/tests/fixtures/state-all-done.yaml`,
    `config/scripts/tests/fixtures/state-escalate.yaml`,
    `config/scripts/tests/golden/*.json`
  - **Why**: FR-1, FR-2, FR-3, FR-9, FR-10, FR-11, FR-12; AC-1, AC-2, AC-8, AC-9
  - **Approach**: 6 fixture state.yamls + matching golden JSON per UC. Test
    runs `python3 config/scripts/orchestrator-next.py <fixture>` and
    byte-compares stdout to the golden file. Also asserts state.yaml mtime
    unchanged and exit codes match.
  - **Verify**: `python3 -m unittest` — all 6 tests FAIL with clear "driver not
    found / not implemented" errors (red for the right reason).

- [ ] T-2 Implement: `orchestrator next` minimal dispatcher (GREEN) (depends: T-1)
  - **Files**: `bin/orchestrator` (Python entry-point, shebang),
    `config/scripts/orchestrator_next/__init__.py`,
    `config/scripts/orchestrator_next/parser.py`,
    `config/scripts/orchestrator_next/dispatch.py`
  - **Why**: FR-1 through FR-4, FR-8 through FR-12
  - **Approach**: Python 3 single package. `bin/orchestrator` starts with
    `#!/usr/bin/env python3`, adds `config/scripts/` to sys.path, imports the
    package, dispatches the `next` subcommand. No bash wrapper — Python is
    the only language for code this feature introduces. `parser.py` loads
    state.yaml via `yaml.safe_load` + step contract YAML for the step head.
    `dispatch.py` is a pure function: State → (action_json, exit_code).
    Main flow wires parse → (skip upsert for now, T-4) → dispatch → print → exit.
  - **Verify**: All 6 T-1 fixture tests pass (green). `state.yaml` mtime
    unchanged under every test.

- [ ] T-3 Write tests: DuckDB `step_events` upsert idempotency (RED) (depends: T-2)
  - **Files**: `config/scripts/tests/test_step_events_upsert.py`,
    additional fixtures under `config/scripts/tests/fixtures/`.
  - **Why**: FR-5, FR-6, FR-7, NFR-1, NFR-2; AC-3, AC-4, AC-5, AC-7
  - **Approach**: Tempfile DuckDB per test. Fixture with N terminal
    step_history entries. Call `orchestrator next` twice; assert
    `SELECT COUNT(*) FROM step_events ... = N`; assert all dimension keys
    non-null; assert short→OTel column mapping on known-value fixture;
    assert inline step produces row with `agent_name='inline'` and NULL
    token columns; assert `change_id` slug-guard rejects bogus inputs.
    **Escalation PK test**: fixture with two step_history entries at the
    same `(phase, step_id, attempt)` — first `status: escalate_to_architect`,
    second `status: completed` — must produce **two** rows in
    `step_events`, not one overwritten. Confirms the composite PK
    includes `status`.
  - **Verify**: Tests FAIL — upsert module does not yet exist.

- [ ] T-4 Implement: DuckDB `step_events` DDL + upsert (GREEN) (depends: T-3)
  - **Files**: `config/scripts/orchestrator_next/upsert.py`,
    `config/scripts/orchestrator_next/otel_map.py`,
    `config/scripts/orchestrator-next.py` (wire upsert into main flow)
  - **Why**: FR-5, FR-6, FR-7; NFR-1, NFR-2
  - **Approach**: `upsert.py` exposes `ensure_schema(db)` and
    `upsert_step_event(db, entry, context)`. DDL uses
    `CREATE TABLE IF NOT EXISTS` with the columns from design.md.
    `otel_map.py` maps short names → OTel column names and serialises
    `tool_calls`/`artifacts` as JSON. All SQL uses parameterised
    `duckdb.execute(sql, params)` — no string interpolation.
    Main flow: parse → `ensure_schema` → iterate terminal step_history
    entries → `upsert_step_event` for each → dispatch.
  - **Verify**: All T-3 tests pass. `python3 -m unittest` fully green.

- [x] T-5 Write tests: Retry / crash-mid-step `attempt` counting (RED) (depends: T-4)
  - **Files**: `config/scripts/tests/test_attempt_counting.py`,
    `config/scripts/tests/fixtures/state-crash-midstep.yaml`,
    `config/scripts/tests/fixtures/state-after-retry-complete.yaml`
  - **Why**: FR-8; AC-8; UC-E1
  - **Approach**: Fixture (a) has `step_history[-1].status=in_progress`,
    no `ended_at`. Expected JSON: `{action: retry_step, attempt: 2,
    previous_failure: "no ended_at"}`. Fixture (b) has attempt:1 failed +
    attempt:2 completed. After `next` runs, DuckDB has two rows; running
    `next` again yields still exactly two rows.
  - **Verify**: Both tests FAIL (retry path not yet implemented).

- [x] T-6 Implement: retry detection + attempt assignment (GREEN) (depends: T-5)
  - **Files**: `config/scripts/orchestrator_next/dispatch.py`
  - **Why**: FR-8; AC-8
  - **Approach**: Add `_compute_attempt(step_history, phase, step_id)` that
    returns `max(existing_attempts) + 1` (defaulting to 1 when none). Add
    retry-detection branch: if `step_history[-1].status == in_progress` and
    no `ended_at`, return `retry_step` action with `previous_failure:
    "no ended_at"` and `attempt` computed from the counter.
  - **Verify**: T-5 tests pass. Re-run full suite — no regressions.

- [x] T-7 Document: update `contracts/error-recovery.md` + `contracts/metrics-schema.md` + new `contracts/step-dispatch.md`
  - **Files**: `config/steps/contracts/error-recovery.md` (modify — add the two
    new `status` values and transitions),
    `config/steps/contracts/metrics-schema.md` (modify — document the `usage:`
    sub-block with short names and the `step_events` table),
    `config/steps/contracts/step-dispatch.md` (create — CLI interface + JSON
    schema + exit codes + the 7 action types)
  - **Why**: FR-12; OQ-3 decision; consumer stability for state.yaml schema
  - **Approach**: Additive-only changes to existing contracts (no breaking
    field changes). New contract file describes the CLI interface in detail
    — this is the authoritative reference for adapter authors.
  - **Verify**: `grep -r "escalate_to_architect" config/steps/contracts/` shows
    the new status appears in both `error-recovery.md` and `step-dispatch.md`.
    `grep "step_events" config/steps/contracts/metrics-schema.md` returns a hit.

- [x] T-8 Write tests: inline-only smoke test across 31 unchanged contracts (RED) (depends: T-6)
  - **Files**: `config/scripts/tests/test_inline_smoke.py`
  - **Why**: FR-14; AC-10; UC-E4; non-regression guard for the 31 inline
    contracts
  - **Approach**: Loop over all `config/steps/*.yaml` without a `run:` field.
    For each, synthesise a minimal state.yaml pointing at it, call
    `orchestrator next`, assert `action: run_inline` and exit 0.
  - **Verify**: Test FAILS initially if any inline contract trips up parsing;
    otherwise it may go green on T-2's implementation — that's acceptable.
    The value is the regression guard it adds once committed.

- [ ] T-9 Implement: reference adapter for `explore` (GREEN) (depends: T-7)
  - **Files**: `config/scripts/adapters/claude_discoverer.py`,
    `config/steps/explore.yaml` (modify — bump version to 3, add
    `run: config/scripts/adapters/claude_discoverer.py`)
  - **Why**: FR-13; AC-6; UC-1; proves the full loop end-to-end
  - **Approach**: Python 3 adapter (consistent with OQ-1; avoids the
    bash/yq quoting class of bugs that drove the Python choice). Reads
    env vars, constructs discoverer prompt from instruction+rules,
    invokes `claude -p` via `subprocess.run`, parses ccusage output for
    tokens/cost, appends a completed `step_history[]` entry to
    `$ORCHESTRATOR_WORKFLOW_DIR/state.yaml` with the full usage block
    using the same YAML-safe round-trip as the CLI.
  - **Verify**: Adapter runs against a scratch workflow dir; a state.yaml
    entry is appended; CLI on next invocation produces a `step_events` row
    with `gen_ai_usage_input_tokens > 0`. Manual smoke OK if CI lacks API
    key; document the manual verification command.

- [ ] T-10 Write test: end-to-end `explore` adapter (RED → GREEN, gated) (depends: T-9)
  - **Files**: `config/scripts/tests/test_explore_adapter.sh`
  - **Why**: AC-6; UC-1
  - **Approach**: Integration test that creates a scratch
    `$WORKFLOW_STATE_DIR` with minimal state.yaml pointing at `explore`,
    runs `orchestrator next`, execs the `run` adapter, re-runs
    `orchestrator next`, and asserts `SELECT gen_ai_usage_input_tokens FROM
    step_events WHERE step_id='explore' LIMIT 1` is non-null and > 0.
    Test skips with a SKIP marker (exit 77) when `CLAUDE_API_KEY` is absent.
  - **Verify**: Test passes locally with API key; skips gracefully in CI.

- [x] T-11 Document: migration guide for remaining step contracts
  - **Files**: `config/steps/contracts/migration-run-field.md` (create)
  - **Why**: Out-of-scope work is documented; unblocks incremental migration
    after signoff
  - **Approach**: Short guide — step-by-step procedure for adding a `run:`
    field, template adapter structure, test checklist, rollback (simply
    remove the `run:` line to revert to inline). Link from
    `config/steps/CONVENTIONS.md`.
  - **Verify**: `ls config/steps/contracts/migration-run-field.md` exists;
    file has all sections.

- [x] T-12 Update `install.sh` for Python dependency
  - **Files**: `install.sh`, `Makefile`
  - **Why**: NFR-3 — installation must not require a heavy setup step
  - **Approach**: Add gated pip install: check `python3 -c "import yaml,
    duckdb"`; if missing, run `pip install --user pyyaml duckdb` and print
    a one-line notice. `make setup` invokes `install.sh` as today; no
    venv, no new Makefile target.
  - **Verify**: `make setup` on a fresh machine without PyYAML/duckdb
    installs both and subsequent `orchestrator next` invocations succeed.

- [ ] T-13 Review checkpoint: phase gate
  - **Why**: Evidence-based completion per `spec/project.yaml` rules
  - **Verify**:
    - `python3 -m unittest discover config/scripts/tests` — all green
    - `orchestrator next` returns the expected JSON for each of the 6
      fixture state.yamls (documented in tasks, reproduced here)
    - `duckdb metrics.duckdb "SELECT COUNT(*) FROM step_events"` returns a
      non-zero row after the end-to-end test
    - No `state.yaml` mtime changed during any dispatcher test
    - Existing `compute-swe-metrics.sh` behaviour unchanged (smoke: run it
      on the `fix-cost-usd-and-widen-token-split` archive; diff produced
      metrics block against the archived one — no drift)

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: dispatch logic 100% branch, upsert 100% branch -->

<!-- VERIFICATION BUGS: If verification reveals new issues, add them as tasks -->
<!-- before proceeding. Do NOT skip ahead. -->
