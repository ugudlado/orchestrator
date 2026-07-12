---
feature-id: orc-115
linear-ticket: ORC-115
---

# Discovery Brief: Per-Step and Workflow-Total Metrics Display

## Feature Summary

ORC-115 adds per-step metrics (duration, split input/output tokens, model used, and cost) plus accumulated workflow totals to orchestrator run output. The ticket was rescoped during investigation to cover both a duration-capture bug (script steps land with `usage: {}` even though `started_at`/`ended_at` are recorded) and the display of all captured metrics in the workflow-report step. Without this, users cannot tell how long individual steps took, which model was used, or how cost accumulated across a multi-schema run.

## Personas & Actors

- **Workflow operator** — engineer running `orchestrator run <id>` locally or in CI, wants to see where time and budget went after a run completes.
- **Orchestrator engine** (`run_loop.py` + `record.py`) — captures usage data as steps complete and writes it into `state.yaml` `step_history[].usage`.
- **workflow-report step** (`workflow_report_step.py`) — reads `step_history` from all state files for the change and renders a summary table.

## Use Cases

### Happy Path

UC-1: Agent step metrics displayed — operator runs a feature workflow; after completion, the workflow-report table shows duration, input tokens, output tokens, model name, and cost for each agent step, plus totals.

UC-2: Script step duration displayed — operator runs a workflow containing script steps (e.g., `create-worktree`, `check-rerun`); the workflow-report table shows non-zero durations for those steps even though they have no LLM usage.

UC-3: Multi-schema totals — operator ran both `design` and `implement` schemas for one ticket; the final workflow-report aggregates all steps across both state files and shows correct accumulated totals.

UC-4: Trigger-chain awareness — operator sees which step triggered a retry loop (e.g., `design-review` on_failure → `design-and-draft-artifacts`) reflected in the attempt count and cumulative cost for the retried step.

### Error & Edge Cases

UC-E1: Missing `usage` field — a step_history entry has no `usage` key or `usage: null`; the report renders `—` for that row's metrics rather than crashing.

UC-E2: Cost fields absent — `cost_usd` and model are missing (e.g., old state file before cost tracking landed); totals compute correctly with those fields treated as 0/unknown.

UC-E3: `duration_ms` present in `usage` but `started_at`/`ended_at` absent — report uses whichever duration source is available without double-counting.

## Scope

### In Scope

- Fix duration capture for script steps: derive `duration_ms` from `ended_at - started_at` when `duration_ms` is absent in `usage`.
- Add `model` column to the workflow-report table (already stored as `usage.model` for agent steps).
- Split `tokens` column into `input` and `output` sub-columns (data is already present as `usage.input_tokens` / `usage.output_tokens`; existing display collapses them).
- Carry `duration_ms` through `record.py`'s `_build_history_entry` into `step_history[].usage` for script steps that currently write `usage: {}`.
- Workflow-report totals: duration (summed), input tokens, output tokens, model (list or most common), total cost.

### Out of Scope

- Persistent metrics storage (DuckDB or any DB) — per ORC decision, console-only reporting is canonical.
- Cross-ticket or cross-repo aggregation — this ticket covers single-run display only.
- Real-time / streaming metrics mid-run — metrics are displayed once at the end in workflow-report.
- UI dashboard or visualization — N/A, CLI only.
- ORC-116 (step briefing/reasoning capture) — depends on ORC-117 and is a separate ticket.
- ORC-117 (engine refactoring for workflow-agnostic output persistence) — separate ticket; ORC-115 should not assume that refactor lands first.

## UI Direction

N/A — no UI components. Output is a formatted table written to stderr by `workflow_report_step.py`, matching the existing `## Workflow step report` style.

## Key Decisions

- KD-1: Derive `duration_ms` at record time in `record.py._build_history_entry` (single source of truth in state.yaml). Resolves OQ-1. Rejected post-hoc derivation in workflow_report because it leaves state.yaml incomplete for other future consumers.
- KD-2: `model` column shows the last model used across attempts (consistent with existing "last status wins" behavior for the collapsed row). Resolves OQ-2.
- KD-3: `input_tokens` / `output_tokens` split columns show cumulative totals across attempts (matches existing tokens/cost cumulative behavior). Resolves OQ-3.
- KD-4: Selected Approach A (record-time derivation + widened table). Approach B (post-hoc derivation only) rejected — cheaper diff but violates single-source-of-truth and duplicates duration logic across write and read paths.

## Open Questions

- OQ-1: Should `duration_ms` for script steps be derived at record time (by computing `ended_at - started_at` in `record.py`) or post-hoc in `workflow_report_step.py`? Deriving at record time is cleaner (single source of truth in state.yaml) but requires touching `record.py`; post-hoc derivation in the report avoids changing the write path.
- OQ-2: For the `model` column, what should be shown when multiple attempts used different models (e.g., a step retried with a fallback model)? Options: last model used, all models comma-joined, or most expensive.
- OQ-3: The current report collapses all attempts for a step into one row. Should split input/output tokens reflect per-attempt or cumulative totals? (Cumulative is current behavior for cost/tokens; keeping it consistent is simplest.)
