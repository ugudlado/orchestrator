# Tasks: Fix 10 Workflow Issues Surfaced in Autopilot 2026-04-19-002

<!-- TDD required: every code change is preceded by a failing test task. -->
<!-- Prose/config edits are verified via grep-assertion tests grouped in one task. -->

## Blocker code fixes (telemetry + dispatch)

- [x] T-1 Write tests: `_migrate_step_events` index/rename sequence (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_upsert_migration.py` (new)
  - **Approach**: Seed a temp DuckDB; create `step_events` with all 5
    otel-prefixed columns AND `idx_step_events_change`; call
    `ensure_schema`; assert (a) no exception, (b) columns are renamed
    to `model`/`input_tokens`/etc., (c) index exists. Also test the
    fast-path: call twice; second call is a no-op and index is not
    dropped in between (observe via a pragma or timing — or by patching
    DROP INDEX to raise and asserting it's not called).
  - **Why**: FR-2, AC-3
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_upsert_migration.py -q` FAILS (red) — implementation not yet changed.

- [x] T-2 Implement: `_migrate_step_events` drop/recreate-index fix (GREEN) (depends: T-1)
  - **Files**: `config/scripts/orchestrator_next/upsert.py` (lines 179–199)
  - **Approach**: Add `_INDEX_NAME = "idx_step_events_change"` constant;
    in `_migrate_step_events`, compute `needs_rename` (any old→new pair
    needs work); if false, return; else `DROP INDEX IF EXISTS`, do
    renames, let caller's `CREATE INDEX IF NOT EXISTS` restore it.
  - **Why**: FR-2 (ISSUE-2 / ISSUE-10.2)
  - **Verify**: T-1 tests pass; full `pytest config/scripts/orchestrator_next/tests/ -q` green.

- [ ] T-3 Write tests: dispatcher phase-transition hint (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_dispatch_phase_hint.py` (new)
  - **Approach**: Build a state.yaml fixture with `workflow_plan`
    containing multiple phases (specify, implement, complete); all
    steps in `specify` complete; `phase: specify`. Call dispatch; capture
    stderr; assert stderr contains `WARNING` and mentions the remaining
    phase names. Also assert the returned action is still
    `complete_workflow` (option c preserves return shape). Second test:
    single-phase plan → no WARNING emitted.
  - **Why**: FR-7, AC-8
  - **Verify**: Tests FAIL (red) pending dispatch.py patch.

- [ ] T-4 Implement: dispatcher WARNING on non-terminal phase completion (GREEN) (depends: T-3)
  - **Files**: `config/scripts/orchestrator_next/dispatch.py` (around line 288)
  - **Approach**: Before `return {"action": "complete_workflow"}, 1`,
    inspect `state.workflow_plan` keys; if > 1 phase and current
    `state.phase` has siblings, print the WARNING to stderr (format per
    design.md §3).
  - **Why**: FR-7 (ISSUE-8 option c)
  - **Verify**: T-3 tests pass.

## Archive cleanup (FR-8)

- [x] T-5 Write test: archive script removes backlog entry (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_archive_backlog_cleanup.py` (new)
  - **Approach**: pytest test that spawns `archive-completed-change.sh`
    via subprocess inside a temp git repo with a dummy `.state/<slug>/`
    and `spec/changes/backlog/<slug>/`. Assert post-run: backlog dir
    absent, cleanup commit present in `git log`, exit 0.
  - **Why**: FR-8, AC-7
  - **Verify**: Test FAILS (red) — script change not yet applied.

- [x] T-6 Implement: archive script backlog cleanup (GREEN) (depends: T-5)
  - **Files**: `scripts/inline/archive-completed-change.sh`,
    `config/steps/archive-completed-change.yaml`
  - **Approach**: Append the cleanup block per design.md §3 after the
    archive commit completes; update contract instruction step 5 to
    name the backlog-dir removal explicitly.
  - **Why**: FR-8 (ISSUE-9)
  - **Verify**: T-5 passes.

- [x] T-7 Data: remove 5 stale backlog entries
  - **Files**: `spec/changes/backlog/{feature-complexity-tracking,
    orchestrator-doctor, per-step-allowed-tools,
    fix-cost-usd-and-widen-token-split,
    tool-calls-rename-and-preview-route-fix}/` (each directory)
  - **Approach**: `git rm -r` each of the 5 directories. Commit
    together with the archive-script change.
  - **Why**: FR-8, AC-7 (one-time data cleanup)
  - **Verify**: `ls spec/changes/backlog/` does not list any of the 5
    names; `git status` clean after commit.

## Prose and contract fixes

- [x] T-8 Write tests: prose / contract grep-assertions (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_prose_contracts.py` (new)
  - **Approach**: One pytest module with per-FR grep assertions:
    - **FR-1**: `agents/workflow-init.md` contains a YAML example
      block with `active:` as a key; contains 0 occurrences of
      `active_steps:` (except in referenced-issue commentary, if any).
    - **FR-3**: `config/steps/preview-route.yaml` has
      `outputs: [route_preview]` (or equivalent list form) and no
      longer contains the literal phrase `state.yaml route_preview block`.
    - **FR-4**: `spec/project.yaml.verify_commands` is non-empty and
      contains a `test:` entry starting with `pytest`.
    - **FR-5**: `skills/orchestrate/SKILL.md` mentions
      `run_in_background: true` AND lists exception agents
      (ideator / reviewer).
    - **FR-6**: `agents/developer.md` contains both `orchestrator
      record` and a prohibition against direct state.yaml edits;
      `agents/workflow-init.md` mirrors the prohibition.
    - **FR-9**: `skills/orchestrate/SKILL.md` `run_step` section has
      a numbered step containing `MANDATORY` and `USAGE CAPTURE`; a
      post-step assertion about `usage.input_tokens` is documented.
    - **FR-10**: `config/steps/compute-swe-metrics.yaml` references
      `scripts/inline/compute-swe-metrics.sh` (not
      `scripts/compute-swe-metrics.sh`).
  - **Why**: FR-1, FR-3, FR-4, FR-5, FR-6, FR-9, FR-10
  - **Verify**: All grep assertions FAIL (red) — prose not yet edited.

- [x] T-9 Implement: `agents/workflow-init.md` edits (GREEN) (depends: T-8)
  - **Files**: `agents/workflow-init.md`
  - **Approach**: (a) In §2, insert a canonical YAML example block
    showing `workflow_plan: {<phase>: {active: [step-a, step-b],
    filtered: []}}` with an explicit pointer that `active:` is the
    key the dispatcher reads. (b) Under constraints or a new "State
    Updates" subsection, add: "MUST use `orchestrator record` for
    step_history appends; MUST NOT directly edit state.yaml with
    Write/Edit tools."
  - **Why**: FR-1, FR-6
  - **Verify**: FR-1 and FR-6 (workflow-init portion) grep-asserts in T-8 pass.

- [x] T-10 Implement: `agents/developer.md` state-update constraint (GREEN) (depends: T-8)
  - **Files**: `agents/developer.md`
  - **Approach**: Add a "State Updates" subsection (or extend "What
    You Don't Do") with: "Use `orchestrator record <state.yaml> <<<
    '{...}'` for all step_history appends. MUST NOT directly edit
    state.yaml with Write/Edit."
  - **Why**: FR-6 (ISSUE-7)
  - **Verify**: T-8 FR-6 grep-assert passes.

- [x] T-11 Implement: `skills/orchestrate/SKILL.md` edits — background spawn, USAGE CAPTURE, phase transitions (GREEN) (depends: T-8)
  - **Files**: `skills/orchestrate/SKILL.md` (§4 run_step + §5 phase transitions)
  - **Approach**: In the `run_step` branch: (a) annotate the spawn
    call with `run_in_background: true` as default, noting
    "exceptions: ideator, reviewer — spawn foreground"; (b) replace
    the USAGE CAPTURE comment block with a numbered step "3.
    MANDATORY: USAGE CAPTURE — extract input_tokens, output_tokens,
    cache_read_input_tokens, cost_usd, duration_ms, tool_calls from
    the task result <usage> block; include under 'usage' in the
    record payload; after record, assert
    step_history[-1].usage.input_tokens is non-null for agent steps."
    In §5 "Phase transitions": add a note about the stderr WARNING
    and the driver's responsibility to update state.yaml `phase` and
    re-dispatch before treating `complete_workflow` as terminal.
  - **Why**: FR-5, FR-7, FR-9 (ISSUE-6, ISSUE-8, ISSUE-10.1)
  - **Verify**: T-8 FR-5, FR-9 grep-asserts pass; §5 contains the
    phase-transition note.

- [x] T-12 Implement: contract and config fixes (GREEN) (depends: T-8)
  - **Files**: `config/steps/preview-route.yaml`,
    `config/steps/compute-swe-metrics.yaml`, `spec/project.yaml`
  - **Approach**: (a) `preview-route.yaml` line 45: replace
    `- state.yaml route_preview block` with `[route_preview]`.
    (b) `compute-swe-metrics.yaml` instruction step 2a: change
    `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh` →
    `$ORCHESTRATOR_HOME/scripts/inline/compute-swe-metrics.sh`.
    (c) `spec/project.yaml` line 134: replace `verify_commands: []`
    with:
    ```yaml
    verify_commands:
      test: pytest config/scripts/orchestrator_next/tests/ -q
    ```
  - **Why**: FR-3, FR-4, FR-10
  - **Verify**: T-8 FR-3, FR-4, FR-10 grep-asserts pass.

## Root-cause validation layer (FR-11)

- [x] T-15 Write tests: `record.py` validation asserts — A, B, C (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_record_validation.py` (new)
  - **Approach**: Three test cases, each driving a rejection path:
    (a) workflow-init completion payload with `workflow_plan: {specify: {active: []}}` →
        expect exit 3, `action: validation_error`, `reason: workflow_plan_active_missing_or_empty`,
        `phases: ['specify']` in result.
    (b) agent-step completion (step_id: explore, agent: discoverer, status: completed)
        with no `usage` key → expect exit 3, `action: validation_error`,
        `reason: agent_step_missing_usage`. Plus a positive case:
        same payload with `usage.input_tokens = 1000` → records cleanly.
    (c) pre-corrupt a state.yaml fixture (truncate mid-key); call record with any
        valid payload → expect exit 4, `action: error`, `reason: state_yaml_parse_failure`.
        Verify the file is restored to its pre-call byte content (byte-equal check).
  - **Why**: FR-11, AC-10
  - **Verify**: All three assertions FAIL (red) — record.py has no validations yet.

- [x] T-16 Implement: `record.py` validation layer — A, B, C (GREEN) (depends: T-15)
  - **Files**: `config/scripts/orchestrator_next/record.py`
  - **Approach**: Add checks A, B, C per design.md §1.5. Order: A and B go between
    lines 71–88 (after existing contract-outputs validation, before state.yaml
    read). C wraps the existing write block: capture `pre_write_bytes` before
    write, `yaml.safe_load` after, restore bytes and return exit 4 on parse failure.
    All three checks exit cleanly with no partial state.
  - **Why**: FR-11 (root cause for ISSUE-1, ISSUE-7, ISSUE-10.1)
  - **Verify**: T-15 tests pass; full `pytest config/scripts/orchestrator_next/tests/ -q` green.

## Phase gate

- [x] T-13 Review checkpoint (phase gate) (depends: T-2, T-4, T-6, T-7, T-9, T-10, T-11, T-12, T-16)
  - **Verify**: Full `pytest config/scripts/orchestrator_next/tests/ -q`
    green (covers T-1, T-3, T-5, T-8 suites + existing tests);
    `orchestrator record` still validates a sample payload; no new
    lint/type warnings on the Python diffs.

## End-to-end telemetry verification

- [x] T-14 Verify end-to-end: run a small workflow, confirm telemetry captures usage, AND confirm record.py validation layer rejects a crafted bad payload (depends: T-13)
  - **Files**: none (verification only; may write `.tmp/` scratch state.yaml)
  - **Approach**: Pick (or seed) a trivial feature slug; run
    `orchestrator next` → spawn an agent step → driver records usage;
    call `orchestrator cost --change-id <slug>`; assert non-empty
    event output. Spot-check state.yaml for `usage.input_tokens`
    present on the step_history entry. Also assert archive flow
    removes the backlog entry for that slug.
  - **Why**: NFR-2, AC-3, AC-4, AC-7
  - **Verify**: `orchestrator cost` output non-empty; state.yaml
    step_history[-1].usage.input_tokens > 0; backlog dir for slug
    absent post-archive.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
