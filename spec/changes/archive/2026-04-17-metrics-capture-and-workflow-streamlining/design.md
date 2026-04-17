# Design: Metrics Capture and Implement-Phase Streamlining

## Context

The orchestrator is a YAML-driven workflow engine. Phases are composed of
step contracts (YAML files in `config/steps/`) that are either:

- **Agent steps** — spawn a named agent (developer, reviewer, researcher,
  architect, discoverer). The Agent tool footer returns total_tokens,
  tool_uses, duration_ms. The dispatch loop writes these into
  step_history[*].usage.
- **Inline steps** — executed directly by the orchestrator dispatch loop
  (no sub-agent). Today these record nothing into step_history[*].usage.

`compute-swe-metrics.sh` runs at the end of the complete phase and parses
state.yaml (step_history + top-level fields) plus Claude Code JSONL
session files to produce the `metrics:` block. It depends on
`completed_at` to bound the JSONL time window; that field is written by
`archive-completed-change`, which currently runs *after* the metrics step.

The implement phase today invokes three reviewer spawns
(`run-simplify`, `run-phase-review`, `run-feature-verification`) plus
optionally `run-ux-critique` — each reviewer re-reads the full diff.

## Goals / Non-Goals

### Goals

- Metrics block consistently shows accurate, non-zero cost and tokens.
- Every step_history entry carries a usage block with at least
  `duration_ms` and `tool_uses` (tokens where available).
- Single reviewer spawn in the implement phase (plus conditional UX critique).
- Per-step aggregation available on every future run via
  `metrics.per_step` without changing the public contract.

### Non-Goals

- Changing the DuckDB ingest schema (dependent ticket).
- Modifying Claude Code tooling or JSONL format.
- Producing token attribution for sub-tool-calls inside an Agent spawn
  beyond what the Agent footer already reports.

## Approaches Considered

### Approach A — Split and Collapse (chosen)

Split `archive-completed-change` into a small inline step that handles
state mutation (`mark-change-completed`) plus a pure file-copy step.
Collapse three reviewer spawns into one `run-implement-review` step that
covers AC verification, 5-dim scoring, and fix-task generation. Developer
simplify pass is an instruction-block append to `execute-next-task` (no
new step file, no new agent spawn). Add a single awk pass for per-step
aggregation.

Pros: minimal structural churn, follows CONVENTIONS "split on unrelated
verbs", preserves single-responsibility.

Cons: one more step file to maintain; awk pass lengthens the metrics
script.

### Approach B — Two reviewer splits

Split implement-phase review into a quality reviewer + a verification
reviewer. Two spawns rather than three. Rejected: fails UC-4
"single reviewer spawn"; more prompt tokens for no real benefit over A.

### Approach C — Keep archive-completed-change unified

Change step ordering alone and keep archive-completed-change writing
completion fields. Rejected: contradicts discovery In-Scope list and
CONVENTIONS "split on unrelated verbs" criterion; leaves the mutate-then-copy
sequence fragile.

### Selected Approach

**Approach A**, chosen in the design-exploration phase, is the smallest
change that satisfies every acceptance criterion without violating
existing conventions.

## High-Level Design

### Architecture Overview

```
┌──────────────────── complete phase ────────────────────┐
│                                                        │
│  compute-prediction-accuracy                           │
│       ↓                                                │
│  run-learn-cycle                                       │
│       ↓                                                │
│  mark-change-completed        (NEW inline step)        │
│     writes: status, completed_at, archive_path         │
│     runs:  field-presence validator (stderr warn)      │
│       ↓                                                │
│  compute-swe-metrics                                   │
│     reads: completed_at (now present)                  │
│     emits: metrics:{..., per_step:{...}}               │
│       ↓                                                │
│  archive-completed-change   (pure move + commit)       │
│       ↓                                                │
│  remove-worktree                                       │
└────────────────────────────────────────────────────────┘

┌──────────────────── implement phase ───────────────────┐
│                                                        │
│  execute-next-task                                     │
│    (loops over tasks; AFTER last task completes, the   │
│     developer runs an appended simplify pass over the  │
│     changed files — no new spawn)                      │
│       ↓                                                │
│  run-ux-critique            (if ux_design: true)       │
│       ↓                                                │
│  run-implement-review       (NEW; single reviewer)     │
│     scope: ACs + 5-dim scoring + fix-task generation   │
└────────────────────────────────────────────────────────┘
```

### Key Abstractions

- **Usage block contract** — every step_history entry carries
  `usage: { duration_ms, tool_uses, [token fields if agent step] }`. The
  dispatch loop is the single writer. Inline steps use `agent: inline`.
- **Per-step aggregation** — a new awk pass in `compute-swe-metrics.sh`
  keys on `step_id` and aggregates the fields above across all executions
  (retry-inclusive).
- **Field-presence validator** — a small shell routine invoked by
  `mark-change-completed` that scans step_history, counts entries missing
  required fields, writes a stderr warning, and always exits 0.

## Low-Level Design

### Components

1. **`config/steps/mark-change-completed.yaml`** (new inline step)
   - Inputs: none.
   - Rules: run before `compute-swe-metrics`; non-blocking validator.
   - Instruction: (a) write top-level `status: completed`,
     `completed_at: <ISO 8601 UTC>`, `archive_path: spec/changes/archive/YYYY-MM-DD-$CHANGE_ID/`;
     (b) run the field-presence validator and log coverage ratio to stderr;
     (c) update step_history per CONVENTIONS.md.
   - Verify: state.yaml has the three fields set; validator exit 0.
   - Outputs: none (pure state mutation).

2. **`config/steps/archive-completed-change.yaml`** (modified)
   - Remove instruction step 2 (state mutation). Retain: rules that
     state.yaml MUST contain metrics and completion fields from prior steps.
   - Retain: create archive dir, copy artifacts, commit, cleanup.
   - Verify: archive dir present; state.yaml in archive already has
     `status: completed` and `metrics:` (checked, not written).

3. **`config/steps/run-implement-review.yaml`** (new agent step — reviewer)
   - Inputs: spec.md, design.md, tasks.md, diff.
   - Scope in the prompt:
     - Verify each acceptance criterion (traced list in output).
     - 5-dimension scoring: correctness, robustness, security,
       maintainability, test-coverage.
     - If issues found, emit fix tasks (appending to tasks.md) per the
       Task Format Contract.
   - Verify: reviewer emits a single structured review record written
     to state.yaml with `review_score:` and optional `fix_tasks:`.

4. **`config/steps/execute-next-task.yaml`** (modified)
   - Append instruction block invoked ONLY when the current iteration is
     the last task: developer runs a simplify pass over
     files-changed-in-this-feature. Same developer agent spawn as the
     task itself — no new spawn.
   - Verify: when last task completes, diff includes simplify-pass
     commits (or a no-op commit if nothing to simplify).

5. **`config/workflows/_complete-phase.yaml`** (modified)
   - Reorder steps to:
     `compute-prediction-accuracy → run-learn-cycle → mark-change-completed
      → compute-swe-metrics → archive-completed-change → remove-worktree`.

6. **`config/workflows/feature.yaml`** (modified)
   - Replace the implement-phase entries `run-simplify`,
     `run-phase-review`, `run-feature-verification` with a single
     `run-implement-review`. Keep `run-ux-critique` conditional on
     `ux_design: true`, positioned between `execute-next-task` and
     `run-implement-review`.

7. **`config/scripts/compute-swe-metrics.sh`** (modified)
   - Add an awk pass that walks step_history, keys on `step_id`, and
     aggregates `total_tokens`, `cost_usd`, `tool_uses`, `duration_ms`,
     `executions`. Emit a YAML `per_step:` block inside `metrics:`.
   - No change to the existing per_agent passes or JSONL parsing.

8. **`config/scripts/backfill-zero-cost-metrics.sh`** (new)
   - Walk `spec/changes/archive/*/state.yaml`.
   - Skip if `metrics.cost.net_usd != 0`.
   - Skip if the matching JSONL directory `$HOME/.claude/projects/<slug>/`
     is absent or empty (log `skip: no-jsonl`).
   - Re-run `compute-swe-metrics.sh <archive-dir>` and replace the
     `metrics:` block in place (atomic rewrite via temp file).
   - Summary line: counts of updated / skipped / failed.

9. **Docs** (modified)
   - `skills/orchestrate/SKILL.md` — new section "Inline-step usage
     schema": duration_ms = completed_at − started_at; tool_uses counted
     by the dispatch loop while executing the inline step; token fields
     omitted (treated as 0).
   - `config/steps/CONVENTIONS.md` — new subsection "Usage block
     contract": every step_history entry MUST include a `usage:` block
     with at least `duration_ms` and `tool_uses`; enumerate the extra
     fields required for agent steps.
   - `config/steps/contracts/metrics-schema.md` — register `per_step`
     in the field registry, mark R across all schemas (inline steps
     appear with zero token fields, not absent).

### Data Flow

1. Dispatch loop starts a step → records `started_at` and `step_id`.
2. Step runs (agent or inline).
3. On step completion the dispatch loop:
   - For agent steps: captures the Agent footer (total_tokens, tool_uses,
     duration_ms, cost_usd) and writes step_history[*].usage including
     tokens.
   - For inline steps: computes `duration_ms = now − started_at`, records
     tool_uses observed during execution, writes step_history[*].usage
     with zero token fields and `agent: inline`.
4. `mark-change-completed` runs the validator → stderr warn if needed.
5. `compute-swe-metrics.sh`:
   - parses step_history as before (fallback path for tokens),
   - attempts JSONL enrichment (now succeeds because `completed_at` is set),
   - runs the new per-step awk pass,
   - emits `metrics:{tokens, cost, ..., per_step:{<step_id>:{...}}}`.
6. `archive-completed-change` copies + commits.

### State Management

- New top-level state.yaml fields written by `mark-change-completed`:
  `status`, `completed_at`, `archive_path` (already in field registry;
  this moves the writer, does not add fields).
- New nested field written by `compute-swe-metrics.sh`:
  `metrics.per_step[*]`. Added to the field registry.
- step_history entries keep their existing shape; inline-step entries
  gain the usage block as a mandatory addition (non-breaking: older
  archives without it continue to parse).

### Error Handling

- Validator failures: warn-only to stderr; exit 0.
- `compute-swe-metrics.sh` per-step pass failure: awk returns empty
  block; existing outputs unaffected.
- Backfill: per-archive failures are logged and skipped; the script
  continues to the next archive and exits 0 unless a top-level error
  occurs (missing tools, write failure to the summary log).
- `run-implement-review` failure: if the reviewer cannot emit structured
  output, the step fails the phase gate (consistent with the current
  behavior of the three separate spawns).

## Constraints

- macOS + Linux compatibility for the awk pass (no GNU-only extensions).
- No new runtime dependencies. `jq` is already required by the existing
  script; no new tools.
- Backward compatibility: older archives without `per_step` must remain
  valid.

## Trade-offs

- The new awk pass lengthens `compute-swe-metrics.sh`. Acceptable: the
  script is the single site for metrics aggregation and has existing
  patterns (per-agent, per-tool) that the new pass mirrors.
- `mark-change-completed` adds one more step file. Acceptable per the
  "split on unrelated verbs" CONVENTIONS rule; the value is a clean
  separation between state mutation and file operations.
- Single reviewer spawn concentrates prompt risk: one bad prompt can
  mask multiple issues. Mitigation: the step contract spells out each
  required output (ACs, 5-dim score, fix tasks) as separate required
  sections; the step gate inspects each.

## Decisions

- Use synthetic `agent: inline` rather than a separate
  `per_inline_steps` block → rationale: reuses the existing per-agent
  awk pass; one less block to reason about → consequence: inline bucket
  appears in `per_agent_tokens` with zero tokens but non-zero duration.
- `per_step` counts retries → better cost signal → consequence: a
  per-step count ≠ a task-completion count; documented in metrics-schema.md.
- Backfill is a post-merge developer script, not a CI gate → JSONL
  availability is non-deterministic for older archives → consequence:
  some archives permanently carry `cost.net_usd: 0`.
- `run-ux-critique` stays a separate conditional step → prompt-bloat
  avoidance → consequence: implement phase may have two reviewer spawns
  (UX + implement) when `ux_design: true`, which is expected and acceptable.
- Simplify pass lives inside `execute-next-task` → no new spawn, simpler
  ordering → consequence: `run-simplify.yaml` is deleted.

## Open Questions

- None remaining from discovery. All six OQs were resolved by the
  design-direction choice (Approach A).

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
