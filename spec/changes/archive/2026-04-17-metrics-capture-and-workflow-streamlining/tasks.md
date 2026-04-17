# Tasks: Metrics Capture and Implement-Phase Streamlining

<!-- TDD: every implementation task has a preceding test task -->
<!-- Task Format Contract: `- [ ] T-N: <description>` with Verify on next line -->

## Phase 1 — mark-change-completed step (AC-1, AC-2, AC-8, AC-9)

- [x] T-1: Write test fixture + test `config/tests/test-mark-change-completed.sh` that constructs a fake state.yaml without `completed_at`, invokes the step's instruction logic, and asserts top-level `status: completed`, `completed_at`, `archive_path` are written (RED — test must fail; step does not yet exist).
  - **Why**: FR-1, AC-1.
  - **Verify**: Test runs and fails for the right reason (step contract absent).

- [x] T-2: Create `config/steps/mark-change-completed.yaml` (inline step) that writes `status: completed`, `completed_at` (ISO 8601 UTC), and `archive_path` to state.yaml (GREEN). (depends: T-1)
  - **Why**: FR-1, AC-1.
  - **Verify**: T-1 passes green.

- [x] T-3: Extend `config/tests/test-mark-change-completed.sh` with a validator case: step_history contains N entries, some missing `usage.duration_ms` or `usage.tool_uses`; assert the step writes a stderr warning naming the coverage ratio and exits 0 (RED). (depends: T-2)
  - **Why**: FR-9, AC-9.
  - **Verify**: Validator case fails (validator not yet implemented).

- [x] T-4: Add the field-presence validator routine to `mark-change-completed.yaml` instruction block (scan step_history, compute coverage ratio, stderr warn, exit 0) (GREEN). (depends: T-3)
  - **Why**: FR-9, AC-9.
  - **Verify**: T-3 validator case passes; existing cases still green.

- [x] T-5: Write regression test `config/tests/test-archive-completed-change-pure.sh` that runs the refactored archive step against a state.yaml that already contains `status/completed_at/archive_path` and asserts the step neither rewrites nor alters those fields (RED — archive step still mutates them). (depends: T-2)
  - **Why**: FR-2, AC-2.
  - **Verify**: Test fails — archive step still writes `completed_at`.

- [x] T-6: Strip state-mutation from `config/steps/archive-completed-change.yaml` (remove instruction step 2 that writes status/completed_at/archive_path); keep move + commit + cleanup (GREEN). (depends: T-5)
  - **Why**: FR-2, AC-2.
  - **Verify**: T-5 passes; archive directory still produced.

## Phase 2 — complete-phase reorder (AC-1, AC-3)

- [x] T-7: Write test `config/tests/test-complete-phase-order.sh` that reads `config/workflows/_complete-phase.yaml` and asserts the step sequence is exactly `compute-prediction-accuracy → run-learn-cycle → mark-change-completed → compute-swe-metrics → archive-completed-change → remove-worktree` (RED). (depends: T-2)
  - **Why**: FR-3, AC-1.
  - **Verify**: Test fails (mark-change-completed not yet in the ordering).

- [x] T-8: Update `config/workflows/_complete-phase.yaml` to insert `mark-change-completed` before `compute-swe-metrics` (GREEN). (depends: T-7)
  - **Why**: FR-3, AC-1.
  - **Verify**: T-7 passes.

- [x] T-9: Write fixture-based test `config/tests/test-compute-swe-metrics-ordering.sh`: build a state.yaml with `completed_at` set and a matching JSONL fixture in a temp `~/.claude/projects/<slug>/` directory; run `compute-swe-metrics.sh` and assert `cost.net_usd > 0`, `tokens.input > 0`, `tokens.output > 0` (RED only if JSONL fixture layout not yet wired; otherwise confirms current happy path). (depends: T-8)
  - **Why**: FR-3, AC-3.
  - **Verify**: Test runs (red if fixture wiring incomplete).

- [x] T-10: Wire the JSONL-fixture harness in the test so the script sees a valid time window and produces non-zero totals (GREEN). (depends: T-9)
  - **Why**: AC-3.
  - **Verify**: T-9 passes.

## Phase 3 — per-step aggregation in compute-swe-metrics.sh (AC-7)

- [x] T-11: Write test `config/tests/test-compute-swe-metrics-per-step.sh` with a fixture state.yaml containing mixed agent + inline step_history entries across three distinct `step_id`s (one repeated for retries); run the script and assert the emitted YAML has a `metrics.per_step:` map with three keys, each containing `total_tokens`, `tool_uses`, `duration_ms`, `executions` (RED). (depends: T-10)
  - **Why**: FR-7, AC-7.
  - **Verify**: Test fails — per_step block not emitted yet.

- [x] T-12: Add the per-step awk pass to `config/scripts/compute-swe-metrics.sh` emitting the `per_step:` YAML block with retry-inclusive execution count (GREEN). (depends: T-11)
  - **Why**: FR-7, AC-7.
  - **Verify**: T-11 passes.

- [x] T-13: Extend the per-step test to assert that the sum of `per_step[*].total_tokens` equals `metrics.tokens.total` within ±1% tolerance (RED if implementation over/under counts). (depends: T-12)
  - **Why**: AC-7.
  - **Verify**: Tolerance assertion passes.

- [x] T-14: Update `config/steps/contracts/metrics-schema.md` to register `per_step` (R across schemas) and document retry-inclusive semantics.
  - **Why**: FR-7, NFR-2.
  - **Verify**: Grep asserts `per_step:` entry in the registry table; semantics paragraph present.

## Phase 4 — usage-block contract + dispatch loop (AC-8, AC-10, AC-11)

- [x] T-15: Write test `config/tests/test-usage-block-contract.sh` that parses a fixture state.yaml from a recent run, asserts every step_history entry has `usage.duration_ms` and `usage.tool_uses`, and counts inline-vs-agent entries. Seed the fixture deliberately with a missing-field row to confirm the test detects gaps (RED — orchestrator skill still permits inline steps without usage). (depends: T-4)
  - **Why**: FR-8, AC-8.
  - **Verify**: Test fails against current orchestrator-generated fixtures.

- [x] T-16: Update `skills/orchestrate/SKILL.md` to require dispatch-loop recording of `duration_ms = completed_at − started_at` and `tool_uses` for every inline step; mark `agent: inline` on inline entries (GREEN). (depends: T-15)
  - **Why**: FR-8, FR-10, AC-8, AC-11.
  - **Verify**: T-15 passes against a freshly generated fixture; grep confirms new skill section.

- [x] T-17: Update `config/steps/CONVENTIONS.md` with a "Usage block contract" subsection enumerating required fields and the inline-step rules.
  - **Why**: FR-10, AC-11.
  - **Verify**: Grep asserts new subsection heading and required-field list.

- [x] T-18: Write test `config/tests/test-per-agent-tokens-coverage.sh` that runs against a fixture state.yaml representing a fresh autopilot and asserts `metrics.per_agent_tokens` contains entries for every distinct agent name in step_history (RED against old fixtures that only cover the proxy path). (depends: T-12)
  - **Why**: AC-10.
  - **Verify**: Test fails with old fixture, passes with the new contract fixture.

- [x] T-19: Verify (no code change expected) that the existing per-agent awk pass already covers every agent when the skill updates (T-16) populate step_history entries for all spawned agents; if a gap is found, amend the awk pass. (depends: T-18)
  - **Why**: AC-10.
  - **Verify**: T-18 passes.

## Phase 5 — implement-phase single reviewer (AC-5, AC-6)

- [x] T-20: Write test `config/tests/test-feature-workflow-review-steps.sh` that parses `config/workflows/feature.yaml` implement phase and asserts: exactly one `run-implement-review`, zero `run-simplify`, zero `run-feature-verification`, and `run-ux-critique` conditional-only on `ux_design: true` (RED). (depends: T-4)
  - **Why**: FR-5, AC-5.
  - **Verify**: Test fails against current feature.yaml.

- [x] T-21: Create `config/steps/run-implement-review.yaml` combining AC verification, 5-dimension scoring, and fix-task generation into a single reviewer spawn (GREEN). (depends: T-20)
  - **Why**: FR-4, AC-5.
  - **Verify**: Step file lints per CONVENTIONS; referenced agent is `reviewer`.

- [x] T-22: Update `config/workflows/feature.yaml` implement phase to replace `run-simplify`, `run-phase-review`, `run-feature-verification` with `run-implement-review`; keep `run-ux-critique` conditional (GREEN). (depends: T-21)
  - **Why**: FR-5, AC-5.
  - **Verify**: T-20 passes.

- [x] T-23: Delete deprecated step files `config/steps/run-simplify.yaml` and `config/steps/run-feature-verification.yaml`.
  - **Why**: Deprecation cleanup (NFR-4 confirms no external consumer).
  - **Verify**: Files absent; feature.yaml references only remaining steps.

- [x] T-24: Write test `config/tests/test-execute-next-task-simplify-pass.sh` that parses `config/steps/execute-next-task.yaml` and asserts an appended simplify-pass instruction block gated on "last task" (RED). (depends: T-4)
  - **Why**: FR-6, AC-6.
  - **Verify**: Test fails.

- [x] T-25: Append the developer simplify-pass instruction block to `config/steps/execute-next-task.yaml` (same developer agent spawn, runs after last task completes) (GREEN). (depends: T-24)
  - **Why**: FR-6, AC-6.
  - **Verify**: T-24 passes; no new agent spawn added to the YAML.

## Phase 6 — backfill script (AC-4)

- [x] T-26: Write test `config/tests/test-backfill-zero-cost.sh` with two fixture archive directories — one with JSONL present, one without — and assert the backfill (a) updates `metrics:` for the JSONL-present archive and (b) skips the JSONL-absent archive, logging `skip: no-jsonl` (RED). (depends: T-10)
  - **Why**: FR-11, AC-4.
  - **Verify**: Test fails — backfill script absent.

- [x] T-27: Create `config/scripts/backfill-zero-cost-metrics.sh` implementing the iteration + re-run + in-place replace logic (GREEN). (depends: T-26)
  - **Why**: FR-11, AC-4.
  - **Verify**: T-26 passes; summary line prints updated/skipped/failed counts.

- [x] T-28: Document the backfill as a post-merge developer action in this ticket's PR description checklist (no code change; marker in the PR body template).
  <!-- POST-MERGE CHECKLIST:
    - [ ] Run: bash config/scripts/backfill-zero-cost-metrics.sh spec/changes/archive
          This re-runs compute-swe-metrics.sh against archived features with
          metrics.cost.net_usd == 0. Archives without JSONL are skipped (logged).
          This is a one-time action. Verify: "updated=N" in the summary line.
  -->
  - **Why**: Decision record: backfill is not a CI gate.
  - **Verify**: PR-description template (or this ticket's eventual PR) includes the backfill checkbox.

## Phase 7 — Phase-gate verification

- [x] T-29: Review checkpoint — run all tests created above (`config/tests/test-*.sh`).
  - **Verify**: All new tests pass; existing `config/tests/` tests still pass; no new shellcheck warnings on modified scripts.

- [x] T-30: Acceptance criteria trace check — run a grep against `.state/metrics-capture-and-workflow-streamlining/spec.md` asserting every AC-N in spec.md appears at least once in the "Why" lines of tasks.md.
  - **Verify**: AC-1 through AC-11 each referenced by at least one task.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
