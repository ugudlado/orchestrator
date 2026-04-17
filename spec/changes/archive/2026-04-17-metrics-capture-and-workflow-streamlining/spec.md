---
feature-id: metrics-capture-and-workflow-streamlining
linear-ticket: HL-285
---

# Specification: Metrics Capture and Implement-Phase Streamlining

## Motivation

During autopilot run HL-282 three related workflow defects surfaced:

1. `compute-swe-metrics.sh` silently emits `cost.net_usd: 0` and zero input/output
   tokens because it runs before `archive-completed-change` writes `completed_at`.
   Without `completed_at`, `parse_session_jsonl` cannot bound the JSONL time
   window and falls through to the state-only fallback path.
2. Per-agent and per-step metrics are grossly under-counted. Inline steps never
   record a `usage:` block, and `compute-swe-metrics.sh` has no per-`step_id`
   aggregation pass, so downstream consumers cannot attribute cost per step.
3. The implement phase spawns three sequential reviewer agents
   (`run-simplify`, `run-phase-review`, `run-feature-verification`) that all
   re-read the same diff — wasteful and expensive where one well-scoped spawn
   would do.

All three share a root: the state.yaml schema produced at workflow runtime
does not carry enough data for post-hoc metrics to be accurate, and the
implement phase has grown reviewer sprawl. The downstream ticket
`duckdb-ingest-normalized-metrics-tables` is blocked until this ships.

## What Changes

- Insert a new inline step `mark-change-completed` before `compute-swe-metrics`
  so that `completed_at`, `status`, and `archive_path` are present when the
  metrics script runs. `archive-completed-change` becomes a pure move+commit.
- Collapse three implement-phase reviewer spawns into a single
  `run-implement-review` step. `run-ux-critique` stays separate (conditional
  on `ux_design: true`).
- Append a developer-driven simplify pass to `execute-next-task` after the
  last task — no new agent spawn.
- Extend `compute-swe-metrics.sh` with a per-`step_id` awk aggregation pass
  that emits a `metrics.per_step:` block.
- Codify the inline-step usage schema (`duration_ms`, `tool_uses`) in
  `skills/orchestrate/SKILL.md` and `config/steps/CONVENTIONS.md`.
- Add a non-blocking state-schema validator that warns when step_history
  entries are missing required usage fields.
- One-time backfill: re-run `compute-swe-metrics.sh` against archived
  features whose `metrics.cost.net_usd == 0` and whose JSONLs still exist.

## Requirements

### Functional

1. **FR-1**: A new step `mark-change-completed` runs in the complete phase
   immediately before `compute-swe-metrics` and writes top-level
   `status: completed`, `completed_at` (ISO 8601 UTC), and `archive_path` to
   state.yaml.
2. **FR-2**: `archive-completed-change` performs only directory creation,
   file copy, commit, and active-dir cleanup. It does not mutate
   `status`, `completed_at`, or `archive_path`.
3. **FR-3**: The complete-phase step order is: `compute-prediction-accuracy →
   run-learn-cycle → mark-change-completed → compute-swe-metrics →
   archive-completed-change → remove-worktree`.
4. **FR-4**: A new step `run-implement-review` combines AC verification,
   5-dimension scoring, and fix-task generation in a single reviewer spawn.
5. **FR-5**: `feature.yaml` implement phase replaces the three reviewer
   steps (`run-simplify`, `run-phase-review`, `run-feature-verification`)
   with a single `run-implement-review` call. `run-ux-critique` remains a
   separate conditional step when `ux_design: true`.
6. **FR-6**: `execute-next-task` appends a developer-driven simplify pass
   that runs after the final task completes, within the same developer
   agent spawn — no new step file and no new agent spawn.
7. **FR-7**: `compute-swe-metrics.sh` emits a `metrics.per_step:` YAML block
   with one entry per distinct `step_id`. Each entry includes at minimum
   `total_tokens`, `tool_uses`, `duration_ms`, and `executions` (retry-inclusive).
8. **FR-8**: Every step_history entry written by the dispatch loop has a
   `usage:` block with at least `duration_ms` and `tool_uses` fields set.
   Inline steps carry `agent: inline` and omit token fields (treated as 0).
9. **FR-9**: `mark-change-completed` runs a non-blocking state-schema
   validator that scans step_history, counts entries missing
   `usage.duration_ms` or `usage.tool_uses`, and writes a stderr warning
   with the coverage ratio. Workflow advances regardless.
10. **FR-10**: `skills/orchestrate/SKILL.md` and `config/steps/CONVENTIONS.md`
    document the inline-step usage schema contract.
11. **FR-11**: A one-time backfill re-runs `compute-swe-metrics.sh` against
    archived features with `metrics.cost.net_usd == 0`. JSONL-missing
    archives are skipped with a log entry; no archive state is corrupted.

### Non-Functional

1. **NFR-1**: Implement-phase wall-clock and token spend drop versus the
   three-spawn baseline (single reviewer spawn replaces three).
2. **NFR-2**: No change to the public `metrics:` contract consumed by the
   DuckDB ingest — `per_step` is additive.
3. **NFR-3**: Validator warnings never block the workflow; all added logic
   is best-effort.
4. **NFR-4**: Backward compatibility: old archived state.yaml files that
   lack `per_step` continue to parse cleanly in existing tooling.

## Architecture

Component boundary changes (see design.md for details):

| File | Status | Role |
|------|--------|------|
| `config/workflows/_complete-phase.yaml` | Modify | Insert `mark-change-completed` before `compute-swe-metrics` |
| `config/steps/mark-change-completed.yaml` | New | Inline step: writes completion fields + runs validator |
| `config/steps/archive-completed-change.yaml` | Modify | Strip state-mutation; keep move+commit+cleanup |
| `config/steps/run-implement-review.yaml` | New | Combined reviewer spawn |
| `config/steps/execute-next-task.yaml` | Modify | Append developer simplify pass after last task |
| `config/workflows/feature.yaml` | Modify | Replace three review steps with one |
| `config/scripts/compute-swe-metrics.sh` | Modify | Add per-step awk pass |
| `skills/orchestrate/SKILL.md` | Modify | Inline-step usage schema |
| `config/steps/CONVENTIONS.md` | Modify | Usage-schema contract |
| `config/steps/contracts/metrics-schema.md` | Modify | Register `per_step` field |
| `config/steps/run-simplify.yaml` | Delete | Absorbed into execute-next-task |
| `config/steps/run-feature-verification.yaml` | Delete | Absorbed into run-implement-review |

## Test Strategy

### Test File Paths

Since this ticket is predominantly shell + YAML + markdown contracts, tests
live alongside the existing `config/tests/` fixtures and script harnesses:

- `config/tests/test-compute-swe-metrics-per-step.sh` — per-step block shape
- `config/tests/test-compute-swe-metrics-ordering.sh` — cost > 0 when
  `completed_at` is present at script-run time
- `config/tests/test-mark-change-completed.sh` — writes expected fields +
  validator coverage ratio on fixtures
- `config/tests/test-feature-workflow-review-steps.sh` — feature.yaml names
  exactly one reviewer spawn plus optional UX critique
- `config/tests/test-backfill-zero-cost.sh` — dry-run behavior against a
  fixture archive directory

### Coverage Targets

Shell scripts exercised by at least one happy path and one
edge/fallback path per new code route. YAML contract changes verified by
structural grep/awk assertions in the test harness.

### Key Test Scenarios

- `compute-swe-metrics.sh` against a fixture state.yaml with `completed_at`
  set and JSONL files present → non-zero input/output tokens and net_usd.
- `compute-swe-metrics.sh` against a fixture state.yaml with mixed
  agent/inline step_history → `per_step` block groups correctly by
  `step_id`; totals sum to `metrics.tokens.total` within ±1% tolerance.
- Field-presence validator emits stderr warning with coverage ratio and
  exits 0 (non-blocking).
- feature.yaml implement phase regex-validated to contain exactly one
  `run-implement-review` and zero `run-simplify`/`run-feature-verification`
  occurrences.
- Backfill script skips archives with no JSONL and leaves their state.yaml
  untouched.

## Acceptance Criteria

- AC-1: Given a feature completing the complete phase, when the orchestrator
  runs `mark-change-completed`, then state.yaml contains top-level
  `status: completed`, `completed_at`, and `archive_path`, and the step runs
  before `compute-swe-metrics`. [traces: UC-1, UC-E2]
- AC-2: Given the refactored `archive-completed-change`, when it executes,
  then it performs only directory copy, commit, and cleanup — it does not
  write `status`, `completed_at`, or `archive_path`. [traces: UC-1]
- AC-3: Given an end-to-end autopilot run whose JSONL files are present,
  when `compute-swe-metrics.sh` completes, then the emitted metrics block
  has `cost.net_usd > 0` and `tokens.input > 0` and `tokens.output > 0`.
  [traces: UC-1]
- AC-4: Given all archived features under `spec/changes/archive/*` with
  `metrics.cost.net_usd == 0` whose JSONL files still exist, when the
  backfill script runs, then each such archive's state.yaml is rewritten
  with non-zero tokens and net_usd; archives without JSONL are skipped and
  logged. [traces: UC-E3]
- AC-5: Given the implement phase, when the workflow runs it to completion,
  then it spawns exactly one reviewer agent via `run-implement-review`
  covering AC verification, 5-dimension scoring, and fix-task generation.
  [traces: UC-4]
- AC-6: Given the final `execute-next-task` iteration, when the last task
  completes, then the developer agent runs an appended simplify pass
  over changed files and the subsequent `run-implement-review` scores the
  simplified code. [traces: UC-5]
- AC-7: Given a completed feature run, when state.yaml is inspected, then
  `metrics.per_step` is present with one entry per distinct `step_id` that
  executed, and the sum of `per_step[*].total_tokens` matches
  `metrics.tokens.total` within ±1% tolerance. [traces: UC-3]
- AC-8: Given a completed workflow run, when step_history is inspected,
  then every entry carries a `usage:` block with at least `duration_ms`
  and `tool_uses` fields (inline steps omit token fields — treated as 0).
  [traces: UC-3]
- AC-9: Given a step_history in which N of M entries are missing the
  required usage fields, when `mark-change-completed` runs the validator,
  then a stderr warning is emitted naming the coverage ratio and the
  workflow continues without failure. [traces: UC-E2]
- AC-10: Given a fresh autopilot run, when it finishes, then
  `metrics.per_agent_tokens` contains one entry per distinct spawned
  agent (not just the proxy path). [traces: UC-2]
- AC-11: Given the updated documentation, when a reader consults
  `skills/orchestrate/SKILL.md` and `config/steps/CONVENTIONS.md`, then
  both describe the usage-block contract including inline-step rules.
  [traces: UC-3, UC-E2]

## Alternatives Considered

**Alternative A (chosen): Split and Collapse.** Split
`archive-completed-change` in two and collapse three reviewer spawns into
one. Lowest-complexity option that satisfies the approved In-Scope list and
the CONVENTIONS.md "split on unrelated verbs" rule.

**Alternative B: Two reviewer splits (quality + verification).** Rejected
as over-engineered. Two spawns would fail UC-4 "single reviewer spawn"
and doubles prompt cost for no real quality gain.

**Alternative C: Inline completion fields inside the existing
`archive-completed-change` without a split.** Rejected because it violates
the "split on unrelated verbs" CONVENTIONS.md rule and leaves the step
mutating state after a file copy — a known source of partial-failure bugs.

## Impact

- **Breaking change**: None to externally consumed contracts.
- **Migration**: One-time backfill script for archived features with
  zero-cost metrics and extant JSONLs.
- **Deprecated files** (delete only after `run-implement-review.yaml`
  accepted): `config/steps/run-simplify.yaml`,
  `config/steps/run-feature-verification.yaml`.

## Decisions

- Use a synthetic `agent: inline` marker for inline steps in step_history
  rather than a separate `per_inline_steps` block — keeps one awk pass.
- `per_step` counts retries — retry-inclusive gives the better cost signal.
- One-time backfill is a post-merge developer action, documented in the
  PR description, not a CI gate (JSONL availability is non-deterministic
  for older archives).
- `run-ux-critique` stays a separate conditional step when `ux_design: true`.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
