---
feature-id: orc-115
linear-ticket: ORC-115
---

# Design: Per-Step and Workflow-Total Metrics Display

## Context

`orchestrator run` records step usage (input/output tokens, model, cost_usd,
duration_ms) into `state.yaml` under `step_history[].usage`, and the
`workflow-report` step reads that history to print a per-step summary table at
the end of a run.

Two gaps exist today:

1. **Duration capture gap** — `record.py._build_history_entry` writes
   `started_at` and `ended_at` on every entry but only surfaces `duration_ms`
   in `usage` when the payload already contains one. Script steps never send a
   `duration_ms`, so `workflow_report_step._render_report` falls back to `—`
   for their duration column even though the wall-clock is derivable from the
   two timestamps already on the entry.
2. **Display gap** — `_render_report` collapses input/output tokens into a
   single `Tokens` column and omits the model entirely, so operators can't
   tell which model ran a step or how the token spend split between input
   (cache-eligible) and output (billed at higher rate).

### Verified System Boundaries

- `orchestrator_next/record.py:517` — `_build_history_entry` is the single
  write site for `step_history[].usage`; verified by `grep -n "step_history"
  orchestrator_next/record.py` — only one caller (`record_step` at line 734).
- `orchestrator_next/record.py:551` — `payload.get("started_at", now)` and
  `"ended_at": now` are already unconditionally set on every entry, so
  `ended_at - started_at` is always computable from the entry itself.
- `config/steps/workflow-report/workflow_report_step.py:78` — the current
  duration source is `usage.get("duration_ms") or 0`; nothing else in the
  report reads timestamps.
- `usage.model` is already populated by `_compute_cost_usd` for agent steps
  (`record.py:539-543`); it is simply not rendered.
- `usage.input_tokens` / `usage.output_tokens` are already present on agent
  entries (validated at `record.py:459` via `_usage_has_tokens`); they are
  collapsed at `workflow_report_step.py:76` into a single sum.

## Goals / Non-Goals

### Goals

- Populate `usage.duration_ms` for every step_history entry, including script
  steps, without touching call sites.
- Show duration, input tokens, output tokens, model, and cost per step in the
  workflow-report table, plus totals for each numeric column.
- Keep the workflow-report table renderable when `usage` is missing, `null`,
  or has partial fields (no crashes on old state files).

### Non-Goals

- No persistent metrics store (per Out of Scope §, console only).
- No cross-ticket / cross-repo aggregation.
- No mid-run streaming metrics — report still fires once at end of run.
- No changes to `pricing.yaml` or `_compute_cost_usd`.
- No ORC-116 (briefing) or ORC-117 (workflow-agnostic persistence) work.

## Approaches Considered

### Approach 1: Derive duration at record time (single source of truth)

Fill `usage["duration_ms"]` inside `_build_history_entry` immediately after
computing `started_at` and `ended_at`, only when the payload did not supply
one. Widen the workflow-report table to show `Model`, `In`, `Out`, `Duration`,
`Cost` columns and totals.

- Pros: `state.yaml` is the single source of truth; any future consumer
  (dashboard, aggregator, telemetry) gets duration for free; the derivation
  lives next to the timestamps that back it, so it can't drift.
- Cons: Touches the write path (small).
- Complexity: S.

### Approach 2: Derive duration only in the reporter (read-side)

Leave `record.py` untouched. In `workflow_report_step._render_report`, fall
back to `(ended_at - started_at)` when `usage.duration_ms` is absent.

- Pros: One-file diff; no risk to the write path.
- Cons: Two duration formulas (write side stays empty, read side computes);
  duplicated in every future consumer; state.yaml still misleading if read
  outside the report.
- Complexity: XS.

### Selected Approach

**Approach 1**. The record path already computes `ended_at` and receives
`started_at`; writing the derived duration one line later costs a handful of
lines and eliminates the follow-on tax on every future consumer that reads
state.yaml. Approach 2 violates single-source-of-truth (KD-1) — the state
file would keep saying duration is unknown for script steps even though it
is trivially derivable.

## High-Level Design

### Architecture Overview

Two mechanical edits and one column widening:

1. `record.py._build_history_entry`: after `usage` is copied from the
   payload, if `duration_ms` is missing and `started_at` + `ended_at` are
   both parseable ISO-8601, set `usage["duration_ms"] = int((ended_at -
   started_at).total_seconds() * 1000)`.
2. `workflow_report_step._render_report`: widen the header + row format to
   emit `Step | Status | Att | Duration | Model | In | Out | Cost`; carry
   `input_tokens`, `output_tokens`, and `model` in the collapsed row dict;
   include per-column totals.
3. Structured `workflow_report.steps[]` output gains `input_tokens`,
   `output_tokens`, and `model` fields alongside the existing `duration_ms`,
   `tokens`, `cost_usd`; totals grow the same two integer fields.

### Key Abstractions

No new abstractions. The change reuses `usage: dict[str, Any]` as the
canonical payload shape and OrderedDict row-collapse pattern already in
place.

## Low-Level Design

### Components

- `orchestrator_next/record.py` — write-path derivation of `duration_ms`.
- `config/steps/workflow-report/workflow_report_step.py` — read-path table
  widening and structured-output expansion.

### Data Flow

1. Step completes → driver POSTs completion payload to `orchestrator done`.
2. `record.py._build_history_entry` builds the `usage` dict, backfills
   `duration_ms` from `ended_at - started_at` when the payload omitted it,
   backfills `model`/`cost_usd` as today.
3. Entry appended to `state.yaml.step_history`.
4. At workflow end, `workflow_report_step` loads all sibling state files,
   collapses attempts by `step_id` (last status wins; cumulative
   duration/tokens/cost), renders the widened table to stderr, emits the
   structured JSON payload for the harness.

### State Management

State-shape delta on `step_history[].usage`:

- `duration_ms: int` — now guaranteed present when both timestamps parse
  (was: only for agent steps that self-reported one).
- Existing fields (`input_tokens`, `output_tokens`, `model`, `cost_usd`,
  `agent_id`) unchanged.

Old state files without `duration_ms` continue to work — the reporter still
reads `usage.get("duration_ms") or 0`.

### Error Handling

- `started_at` or `ended_at` unparseable → skip derivation, leave
  `duration_ms` absent (report shows `—`).
- `usage` absent or `null` on the entry → render `—` in every metric column
  (already covered by `entry.get("usage") or {}` guard).
- `model` absent → render `—` in the Model column.
- Zero tokens on a script step → render `—` in `In`/`Out` (matches current
  `if tokens else "—"` pattern).

## Constraints

None beyond standard project conventions (evidence-based, minimal-diffs,
state-sync).

## Trade-offs

- Widening the table pushes total width from ~87 to ~110 chars; acceptable
  because the report already targets a terminal (stderr) and existing
  step_ids can reach 35 chars. Column widths are tuned to keep the model
  cell compact (`sonnet-4-5` etc.).
- Cost column keeps 4-decimal precision, matching current output; per-token
  cost columns are not added (redundant with tokens × pricing.yaml).

## Acceptance Criteria

- AC-1: Given a workflow run whose `step_history` includes an agent step
  with `usage.input_tokens`, `usage.output_tokens`, `usage.model`, and
  `usage.cost_usd` populated, when the workflow-report step renders, then
  the step's row shows a non-`—` value in each of the Duration, In, Out,
  Model, and Cost columns. [traces: UC-1]
- AC-2: Given a script step whose completion payload sets no
  `usage.duration_ms` but whose entry has both `started_at` and `ended_at`
  as parseable ISO-8601 timestamps, when `record.py` builds the history
  entry, then `usage.duration_ms` equals
  `int((ended_at - started_at).total_seconds() * 1000)` and the reporter
  shows a non-`—` Duration for that row. [traces: UC-2, UC-E3]
- AC-3: Given multiple state files for a single change_id (e.g., a
  design-schema and an implement-schema run), when workflow-report renders,
  then the TOTAL row's Duration, In, Out, and Cost sum across every entry
  from every sibling state file. [traces: UC-3]
- AC-4: Given a step retried across multiple attempts, when workflow-report
  collapses the entry rows, then the row's Attempts count is the maximum
  attempt value observed, In/Out/Cost/Duration are cumulative across all
  attempts, and Model shows the model recorded on the latest attempt.
  [traces: UC-4, OQ-2, OQ-3]
- AC-5: Given a step_history entry with no `usage` key (or `usage: null`),
  when workflow-report renders, then the row's Duration, In, Out, Model,
  and Cost cells all show `—` and no exception is raised. [traces: UC-E1]
- AC-6: Given a step_history entry whose `usage` has neither `model` nor
  `cost_usd` (old file predating cost tracking), when workflow-report
  renders totals, then those fields contribute 0 to the totals and no
  exception is raised. [traces: UC-E2]

## Decisions

- KD-1 (derive at record time) → single source of truth in state.yaml →
  every downstream consumer inherits the fix for free.
- KD-2 (last model wins on collapse) → mirrors existing "last status wins"
  → keeps the collapse function trivial and consistent.
- KD-3 (cumulative in/out tokens across attempts) → matches existing
  cumulative tokens/cost behavior → no divergent per-column policy.

## Open Questions

- None — OQ-1, OQ-2, OQ-3 resolved as KD-1/KD-2/KD-3.

<!-- Format contract: config/steps/design-and-draft-artifacts/prompt.md § Design Format Contract -->
