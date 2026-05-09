---
feature-id: orc-39
linear-ticket: HL-304
---

# Specification: Metrics capture and implement-phase streamlining

## Motivation

The orchestrator's metrics pipeline produces incomplete or incorrect data on a per-feature basis. Three concrete defects degrade the self-improving feedback loop:

1. **Stale metrics snapshot** — the `metrics:` block written into archived `state.yaml` shows `api_calls: 0` and zero benchmark ratios because `compute-swe-metrics` runs before subagent rows are committed and (for some features) before any `feature_metrics` row exists.
2. **Agent attribution corruption** — every step (including ones whose step contract declares `agent: developer`, `architect`, etc.) is recorded under `agent_name='inline'` in `step_events`. `per_agent_tokens` in `feature_report` collapses to a single `inline` bucket, masking real agent cost distribution.
3. **No opt-out for closing-phase spend** — `run-learn-cycle` and the `FINAL-TASK SIMPLIFY PASS` are unconditionally executed on every feature completion, even for tiny changes where their value is below their cost.

Diagnosis confirms each via live DuckDB queries against `metrics.duckdb`. Discovery established that all three are scoped to existing files; no new components are needed.

## What Changes

- `compute-swe-metrics` and the FEATURE-boundary subagent write are reordered so the metrics snapshot is captured AFTER subagent rows are committed but BEFORE archive copies the snapshot.
- `record.py` cross-validates the payload `agent` field against the step contract's declared agent, rewriting `agent: inline` to the contract's agent when the contract specifies a non-inline agent.
- A new `gates.learn` flag is registered in `config/flags.yaml`, gating `run-learn-cycle`. A new behavioral flag `simplify` is registered, gating the prose simplify clause embedded in `execute-next-task.yaml`.
- The `run-learn-cycle.yaml` learned-rule prose is amended to enumerate `flags.learn=false` as a valid skip reason.
- The `mark-change-completed.sh` direct-write flow is preserved; what changes is the order of metrics emission so the snapshot is correct.

## Requirements

### Functional

1. **FR-1**: After the complete phase finishes successfully, `step_events` contains one row per subagent spawned during the feature with non-null `turns` and `input_tokens`, and `feature_report.turns` for the feature equals the sum of those subagent rows' turns.
2. **FR-2**: For every step whose step-contract declares a non-inline `agent:` (e.g. `developer`, `architect`, `discoverer`, `workflow-improver`), the corresponding `step_events.agent_name` matches the contract's value, never `inline`.
3. **FR-3**: When `flags.learn=false` is resolved by `workflow-init`, `workflow_plan.complete.active` does NOT include `run-learn-cycle`; `run-learn-cycle` appears in `workflow_plan.complete.filtered` with `reason: "flag learn=false"`.
4. **FR-4**: When `flags.simplify=false` is resolved by `workflow-init`, the developer agent reads the flag from `state.yaml` and skips the FINAL-TASK SIMPLIFY PASS clause (no commit with message starting `chore(...): simplify pass after final task` is produced).
5. **FR-5**: The archived `state.yaml.metrics` block in `spec/changes/archive/<date>-<slug>/state.yaml` contains `api_calls > 0` whenever the feature spawned at least one subagent.
6. **FR-6**: `feature_metrics.tasks_total` is non-null for every feature that has a tasks.md with at least one task on completion.

### Non-Functional

1. **NFR-1**: All changes preserve the existing `orchestrator done` contract — no new required payload fields.
2. **NFR-2**: `step_events` PK `(repo_root, change_id, phase, step_id, attempt, status)` keeps boundary writes idempotent — re-running boundary logic must not insert duplicates.
3. **NFR-3**: Defaults preserve current behavior: `gates.learn.default = true`, `behavioral.simplify.default = true`. Existing autopilot runs see no behavior change unless flags are explicitly flipped.

## Architecture

### File modification table

| File | Change |
|---|---|
| `config/scripts/orchestrator_next/record.py` | Trigger `_write_subagent_events` (and optionally `_write_driver_session`) at the Phase 5 mark-change-completed path so subagent rows are in DB before `compute-swe-metrics` queries the view. Cross-validate payload `agent` against step contract; rewrite to contract's agent on mismatch. |
| `config/flags.yaml` | Add `gates.learn: { steps: [run-learn-cycle], default: true }`. Add `behavioral.simplify: { default: true, description: ... }`. Register `--no-learn` and `--no-simplify` CLI mappings. |
| `~/.config/orchestrator/config/steps/run-learn-cycle.yaml` | Amend the learned rule on line 15 to add `flags.learn=false` to the enumerated skip-reason list. No instruction changes (the gate filter happens in workflow_plan generation, not in the step). |
| `~/.config/orchestrator/config/steps/execute-next-task.yaml` | Wrap the FINAL-TASK SIMPLIFY PASS prose (lines 146-160) in a `If flags.simplify is false, skip steps 10a-d` conditional that the developer agent evaluates against `state.yaml.flags`. |
| `config/scripts/orchestrator_next/tests/test_record_validation.py` | Add a regression test for agent-rewrite behavior. |
| `config/scripts/orchestrator_next/tests/test_boundary_detection.py` (or new `test_phase5_subagent_write.py`) | Add a regression test that subagent rows are committed at mark-change-completed. |
| `config/scripts/orchestrator_next/tests/test_generate_plan.py` | Add a regression test that `flags.learn=false` filters `run-learn-cycle` out of `workflow_plan.complete.active`. |

### Data flow

Before:
```
mark-change-completed → compute-swe-metrics → archive-completed-change → remove-worktree
            (Phase 5)        (queries DB)         (copies state.yaml)         (FEATURE boundary writes subagent rows)
```
At `compute-swe-metrics` time the subagent rows are not yet in `step_events`, so `feature_report.turns = 0` is captured into the snapshot.

After:
```
mark-change-completed → compute-swe-metrics → archive-completed-change → remove-worktree
   (Phase 5 + subagent              (snapshot now sees       (copies correct           (re-runs subagent write
    rows committed here)             real subagent rows)      snapshot)                — idempotent no-op upsert)
```

## Test Strategy

### Test file paths

| Component | Test file |
|---|---|
| Phase 5 subagent write | `config/scripts/orchestrator_next/tests/test_phase5_subagent_write.py` (new) |
| Agent-name rewrite | `config/scripts/orchestrator_next/tests/test_record_validation.py` (extend) |
| Flag gate filtering | `config/scripts/orchestrator_next/tests/test_generate_plan.py` (extend) |

### Coverage targets

- `record.py` Phase 5 path: every new branch covered by at least one test.
- `record.py` agent-rewrite logic: tests for matching contract, mismatching contract, missing contract.
- `generate_plan.py` gate evaluation: test for `flags.learn=false`.

### Key test scenarios

- Subagent rows are present in `step_events` when `compute-swe-metrics` queries `feature_report`.
- A payload with `agent: inline` for a step whose contract says `agent: developer` produces a row with `agent_name='developer'` (rewrite + warning).
- A payload with `agent: inline` for a step whose contract has no `agent:` (truly inline step like `mark-change-completed`) produces a row with `agent_name='inline'` (no rewrite).
- `workflow_plan.complete.active` excludes `run-learn-cycle` when `flags.learn=false`.

## Acceptance Criteria

- AC-1: Given a feature run completes through the complete phase, when `compute-swe-metrics` queries `feature_report`, then the `turns` value equals the sum of `step_events.turns` across all subagent rows for that change_id (no longer zero). [traces: UC-1]
- AC-2: Given a feature whose `execute-next-task` step contract declares `agent: developer`, when the step is recorded via `orchestrator done`, then the resulting `step_events.agent_name` is `'developer'`. [traces: UC-2]
- AC-3: Given an inline step (e.g. `mark-change-completed`) whose contract has no `agent:` field, when the step is recorded, then `step_events.agent_name` remains `'inline'`. [traces: UC-2, UC-E2]
- AC-4: Given `flags.learn=false` in CLI input to `workflow-init`, when `workflow_plan` is computed, then `workflow_plan.complete.active` does not list `run-learn-cycle` and `workflow_plan.complete.filtered` lists it with reason `"flag learn=false"`. [traces: UC-4]
- AC-5: Given `flags.simplify=false` in `state.yaml` at the time the final task is executed, when `execute-next-task` runs the FINAL-TASK SIMPLIFY PASS clause, then no `chore(<change-id>): simplify pass after final task` commit appears in the feature branch. [traces: UC-3]
- AC-6: Given a feature whose tasks.md contains N tasks (N ≥ 1) on completion, when the complete phase finishes, then `feature_metrics` has a row for the change with `tasks_total = N`. [traces: UC-1, UC-E1]
- AC-7: Given default flags (`learn=true`, `simplify=true`), when the complete phase runs, then `run-learn-cycle` appears in `workflow_plan.complete.active` and the FINAL-TASK SIMPLIFY PASS clause is not skipped. [traces: UC-1] (regression guard for NFR-3)
- AC-8: Given the archived `state.yaml` for a feature that spawned at least one subagent, when the `metrics:` block is read, then `api_calls > 0`. [traces: UC-1]

## Alternatives Considered

**Alternative 1: Replicate the feature_metrics write inside `mark-change-completed.sh` instead of relying on Phase 5.**
Rejected. Duplicates record.py logic in shell, increases drift risk, and Phase 5 already exists and works for the features where started_at/completed_at are present. The actual fix is to make Phase 5 also commit subagent rows.

**Alternative 2: Reorder `_complete-phase.yaml` to put `compute-swe-metrics` AFTER `remove-worktree`.**
Rejected. `archive-completed-change` reads the `metrics:` block from `state.yaml` and copies it; `remove-worktree` deletes the worktree's state. Putting compute-swe-metrics after remove-worktree breaks the archive copy of metrics.

**Alternative 3: Hard-reject payloads that self-report `agent: inline` for non-inline contracts.**
Rejected. The diagnose phase explicitly flagged "is the self-reporting intentional for steps running in the driver session?" as an open question. A hard reject would break every in-flight workflow until callers update. The chosen approach is rewrite-with-warning: backwards-compatible, surfaces drift in logs, fixes attribution.

**Alternative 4: Add per-step token measurement to inline steps (Defect 2a in discovery).**
Rejected. Project learning `inline-steps-are-tokenless` (2026-04-18) documents that the parent-context token counter is not exposed to in-session inline execution. This is an architectural constraint, not a fixable bug. Documented as known limitation, no fix attempted.

## Impact

- **Breaking changes**: None. Defaults preserve current behavior.
- **Migration**: No data migration. Existing rows in `step_events`/`feature_metrics` are not rewritten retroactively.
- **Affected areas**: Complete-phase steps; the orchestrator dispatcher's flag-gate machinery; the developer agent's prose-driven simplify pass.

## Decisions

- **Decision: Move subagent-row commit to Phase 5 (mark-change-completed)** → rationale: it is the earliest point where the workflow is logically complete and the JSONL session log is parseable; the existing FEATURE-boundary write at remove-worktree becomes an idempotent no-op upsert (PK is stable). → consequence: compute-swe-metrics sees correct turns counts when it queries feature_report.
- **Decision: Rewrite agent name (warn) rather than reject (fail)** → rationale: backwards-compatible, surfaces drift in logs, addresses the open question conservatively. → consequence: developers see a `[record] agent rewritten: contract=X payload=inline` warning when contracts and payloads disagree, and per_agent_tokens is correct.
- **Decision: Use existing flag-gate registry pattern (`gates.learn`)** rather than ad-hoc gating in run-learn-cycle.yaml → rationale: zero new code paths in generate_plan.py — the gate machinery already exists for `worktree`, `phase_review`, etc. → consequence: `--no-learn` becomes a real CLI flag with a registered default.
- **Decision: Defer per-step inline tokenlessness as a documented constraint** → rationale: per project learning 2026-04-18 — fundamental architectural limit, not in scope. → consequence: per-step breakdown for inline steps remains zero; total session cost is captured at FEATURE boundary via driver_sessions.
