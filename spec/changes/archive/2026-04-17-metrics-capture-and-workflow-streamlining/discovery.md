---
feature-id: metrics-capture-and-workflow-streamlining
linear-ticket: HL-285
---

# Discovery Brief: Metrics Capture and Implement-Phase Streamlining

## Feature Summary

Three tightly-coupled workflow defects discovered during HL-282 (autopilot-2026-04-17-001): (1) `compute-swe-metrics.sh` silently produces zero cost and zero input/output tokens because it runs before `archive-completed-change` writes `completed_at`, which `parse_session_jsonl` requires to bound the JSONL time window; (2) per-agent and per-step metrics are grossly under-counted because inline steps never record usage and `compute-swe-metrics.sh` has no per-`step_id` aggregation pass; (3) the implement phase spawns three sequential reviewer agents that re-read the same files, where one well-scoped spawn would do. All three share a root: the state.yaml schema written at workflow runtime does not carry enough data for post-hoc metrics to be accurate.

## Personas & Actors

- Workflow orchestrator (automated dispatch loop in `skills/orchestrate/SKILL.md`) — writes step_history entries and drives the complete phase
- Developer agent — executes tasks and currently owns `run-simplify`
- Reviewer agent — currently spawned three times in the implement phase
- `compute-swe-metrics.sh` — shell script that reads archived state.yaml and JSONL files
- `duckdb-ingest-normalized-metrics-tables` — downstream ticket, consumes the metrics block; blocked until this ticket ships

## Use Cases

### Happy Path

UC-1: Accurate cost metrics on archive — the orchestrator completes a feature, archives it, and the resulting state.yaml shows non-zero `metrics.cost.net_usd` and `metrics.tokens.input/output` derived from the JSONL session files (not from the state-only fallback).

UC-2: Full per-agent coverage — after a fresh autopilot run, `metrics.per_agent_tokens` contains one entry per distinct spawned agent (20+ entries for a typical autopilot), not just 2–3 from the proxy path.

UC-3: Per-step aggregation available — the archived state.yaml contains a `metrics.per_step` block with one entry per distinct `step_id` that executed, including inline steps (no token fields for inline, but duration and tool counts are present).

UC-4: Single reviewer spawn — an implement-phase run fires exactly one reviewer-agent spawn that covers AC verification, 5-dimension scoring, and fix-task generation.

UC-5: Developer simplify pass — after the last execute-next-task iteration completes, the developer agent runs a simplification pass over the changed files before the reviewer is invoked.

### Error & Edge Cases

UC-E1: JSONL absent — when `~/.claude/projects/<slug>/` does not exist (CI, older checkout, or different tool), `parse_session_jsonl` returns 1 and the script falls back to state.yaml step_history totals. With Task C in place, step_history has real token data for agent steps, so the fallback produces accurate aggregates rather than zeros.

UC-E2: Validator coverage warning — `mark-change-completed` detects step_history entries missing `usage.duration_ms` or `usage.tool_uses` and emits a non-blocking stderr warning listing coverage ratio. Workflow advances normally.

UC-E3: Backfill JSONL-time-window miss — archived features where `completed_at` was absent at metrics-compute time have `cost.net_usd: 0`. After this fix, a one-time backfill script re-runs `compute-swe-metrics.sh` against those archives; if JSONL files are no longer present, the backfill leaves the record as-is and logs a skip.

## Scope

### In Scope

- `config/workflows/_complete-phase.yaml` — insert `mark-change-completed` step before `compute-swe-metrics`; step ordering change is the root fix for Issue 1
- `config/steps/mark-change-completed.yaml` — new inline step: writes `status: completed`, `completed_at`, `archive_path` to state.yaml; runs field-presence validator (C.2)
- `config/steps/archive-completed-change.yaml` — strip state-mutation responsibility (steps 2a/2b that write `completed_at`); becomes a pure move+commit step
- `config/workflows/feature.yaml` — collapse `run-simplify` + `run-phase-review` + `run-feature-verification` to a developer simplify append inside `execute-next-task` + single `run-implement-review` spawn
- `config/steps/run-implement-review.yaml` — new step combining AC verification, 5-dimension scoring, fix-task generation; replaces three separate reviewer spawns
- `config/steps/execute-next-task.yaml` — append developer-driven simplify pass after all tasks complete (no new agent spawn; runs as an extra instruction block in the developer agent)
- `config/scripts/compute-swe-metrics.sh` — add per-step awk aggregation pass (C.3) emitting `per_step:` block; keep existing per-agent passes
- `skills/orchestrate/SKILL.md` — codify full usage schema for inline steps: `duration_ms = completed_at − started_at`, tool counts from dispatch-loop-visible calls
- `config/steps/CONVENTIONS.md` — add section: "Every step writes a complete usage: block" with the minimum required fields
- One-time backfill: re-run `compute-swe-metrics.sh` against all `spec/changes/archive/*/state.yaml` where `metrics.cost.net_usd == 0`

### Out of Scope

- Agent tool changes — no modifications to Claude Code tooling or JSONL format (this fix works with what the tool already emits)
- DuckDB schema changes — `duckdb-ingest-normalized-metrics-tables` is a dependent ticket; adding `per_step` to the DuckDB normalized tables is deliberately deferred to keep this PR reviewable
- `config/steps/run-simplify.yaml` and `run-feature-verification.yaml` — deprecated (file deletion is in scope, but only after `run-implement-review.yaml` is accepted)
- `specify`-phase `run-phase-review.yaml` — untouched; the simplification only affects the implement phase
- Per-step attribution for agent-spawned sub-steps that the dispatch loop cannot observe (e.g., tool calls made inside an Agent subagent's execution are already aggregated in the Agent footer; no change here)
- Token-level per-step breakdown for inline steps — inline steps don't spawn agents, so only `duration_ms` and `tool_uses` are reliably measurable; `total_tokens` stays zero for inline

## UI Direction

N/A — no UI components. All changes are to YAML step contracts, a shell script, and skill/agent markdown definitions.

## Key Decisions

**Chosen design direction: Approach A — "Split and Collapse"** (complexity M = 3)

Core shape:
- Split `archive-completed-change` into two steps: new inline `mark-change-completed` (writes `status`, `completed_at`, `archive_path`, runs usage-field validator) runs BEFORE `compute-swe-metrics`; `archive-completed-change` becomes a pure move+commit step.
- Collapse the three implement-phase reviewer spawns (`run-simplify`, `run-phase-review`, `run-feature-verification`) into a single new `run-implement-review` step covering AC verification, 5-dimension scoring, and fix-task generation. `run-ux-critique` remains a separate conditional step when `ux_design: true` (resolves OQ-5 — keep UX critique separate to avoid prompt bloat in the combined reviewer).
- Developer simplify pass is an instruction-block append to `execute-next-task` (no new step file, no new agent spawn) — resolves OQ-3 toward the simpler form.
- `compute-swe-metrics.sh` gains a new awk pass producing `metrics.per_step` keyed on `step_id`. Inline steps bucket into `per_agent_tokens` under synthetic `agent: inline` (no separate block) — resolves integration point #4.
- Inline step usage schema: `duration_ms = completed_at − started_at`; `tool_uses` counted by the dispatch loop; `total_tokens` omitted/zero for inline. Codified in `skills/orchestrate/SKILL.md` and `config/steps/CONVENTIONS.md`.
- One-time backfill is a post-merge developer action documented in the PR description, not a CI gate — resolves OQ-6 (JSONL availability is non-deterministic for older archives).
- `per_step` aggregation counts all executions including retries — resolves OQ-4 toward the better cost signal.

Auto-selection rationale:
- Lowest-complexity option (Approach C, S=2) was disqualified because it contradicts the approved discovery In-Scope list (which mandates the step split and the new `mark-change-completed` and `run-implement-review` contracts) and violates CONVENTIONS.md "split on unrelated verbs".
- Approach A selected as the lowest-complexity option that satisfies the approved scope and the split-responsibility convention.
- Approach B (L=4) rejected as over-engineered — two reviewer splits would fail UC-4 "single reviewer spawn".

Open questions still to resolve in spec/design:
- OQ-1: Resolved — split into two steps (per above).
- OQ-2: Design will specify dispatch-loop-observed tool counts for inline steps (observable in principle per integration point #3); no estimation flag.
- OQ-3: Resolved — instruction-block append, no new step file.
- OQ-4: Resolved — retry-inclusive.
- OQ-5: Resolved — keep `run-ux-critique` separate.
- OQ-6: Resolved — post-merge backfill, not CI.

## Open Questions

- OQ-1: Should `mark-change-completed` and `archive-completed-change` be combined into a single step (reducing spawn count) or kept separate (honoring single-responsibility per CONVENTIONS.md)? The existing `archive-completed-change` currently does both state mutation and file operations — CONVENTIONS.md § "When to split a step" explicitly calls out "intent has two unrelated verbs" as a split criterion, which supports the split. But a new inline step adds one more schema entry to maintain.

- OQ-2: Should inline step tool counts be estimated from the dispatch loop (counting Read/Bash/Edit/Grep calls the orchestrate agent makes while executing the step inline) or be recorded as zero with a flag? The dispatch loop executes inline steps in its own context, so counts are observable in principle, but whether the LLM accurately attributes calls to the correct step is uncertain.

- OQ-3: Is "developer simplify pass" a new instruction block appended to `execute-next-task` (no new step file, simpler) or a new `inline-simplify` step in the schema (explicit, auditable in step_history)? The backlog proposal says "no new agent spawn" but doesn't commit to form.

- OQ-4: Does `per_step` aggregation accumulate across retries (i.e., `execute-next-task` count reflects all attempts) or only successful executions? Retry-inclusive counting gives a better signal on total cost; retry-exclusive matches the task-completion framing.

- OQ-5: `run-ux-critique` sits between `run-simplify` and `run-phase-review` in `feature.yaml` when `ux_design: true`. The backlog proposal says "collapse 3 reviewer spawns" but doesn't address UX critique. Does the new `run-implement-review` absorb UX-critique duties, or does `run-ux-critique` remain as a separate conditional step when `ux_design: true`?

- OQ-6: For the backfill of zero-cost archives, should the re-run happen as part of this PR's acceptance criteria (in-CI), or as a post-merge developer action documented in the PR description? JSONL files for older features may no longer be available, making CI backfill partially deterministic.

## Technical Context

### Affected Files

| File | Status | Role |
|------|--------|------|
| `/Users/spidey/code/orchestrator/config/workflows/_complete-phase.yaml` | Modify | Step ordering; insert `mark-change-completed` |
| `/Users/spidey/code/orchestrator/config/scripts/compute-swe-metrics.sh` | Modify | Add per-step awk pass; reads `completed_at` (already present after reorder) |
| `/Users/spidey/code/orchestrator/config/steps/archive-completed-change.yaml` | Modify | Strip state-mutation; keep move+commit |
| `/Users/spidey/code/orchestrator/config/steps/mark-change-completed.yaml` | New | Inline step: writes `completed_at`, runs field validator |
| `/Users/spidey/code/orchestrator/config/steps/run-simplify.yaml` | Deprecate/Delete | Absorbed into execute-next-task developer pass |
| `/Users/spidey/code/orchestrator/config/steps/run-phase-review.yaml` | Keep (specify phase only) | Not changed; implement-phase use replaced |
| `/Users/spidey/code/orchestrator/config/steps/run-feature-verification.yaml` | Deprecate/Delete | Absorbed into run-implement-review |
| `/Users/spidey/code/orchestrator/config/steps/run-implement-review.yaml` | New | Combined reviewer spawn for implement phase |
| `/Users/spidey/code/orchestrator/config/steps/execute-next-task.yaml` | Modify | Append developer simplify pass after last task |
| `/Users/spidey/code/orchestrator/config/workflows/feature.yaml` | Modify | Replace 3 implement-phase review steps |
| `/Users/spidey/code/orchestrator/skills/orchestrate/SKILL.md` | Modify | Inline step usage schema |
| `/Users/spidey/code/orchestrator/config/steps/CONVENTIONS.md` | Modify | Document usage schema contract |
| `/Users/spidey/code/orchestrator/config/steps/contracts/metrics-schema.md` | Modify | Add `per_step` to field registry and per-schema variants table |

### Key Integration Points

1. **`_complete-phase.yaml` step ordering** — `compute-swe-metrics.sh` line 210: `if command -v jq ... && [[ -n "$STARTED_AT" && -n "$COMPLETED_AT" ]]` — `COMPLETED_AT` is extracted at line 205 via `grep '^completed_at:' "$STATE_FILE"`. Currently this is empty when the script runs; after the fix, `mark-change-completed` will have written it before the script runs.

2. **`archive-completed-change.yaml` instruction step 2** currently writes `status: completed`, `completed_at`, and `archive_path`. The note in that step already documents the ordering dependency: "completed_at is required by compute-swe-metrics.sh — without it, parse_session_jsonl cannot bound the JSONL time window." Moving the write to `mark-change-completed` resolves this without breaking the archive copy (step 4 in that file copies whatever is currently in state.yaml, so completed_at will be present before the copy happens).

3. **Dispatch loop usage extraction** (`skills/orchestrate/SKILL.md` lines ~175–200) — the existing logic handles agent steps. Inline step timing requires the dispatch loop to record `started_at` before inline execution and `completed_at` after (already done per the dispatch loop spec). Tool counts for inline steps are made by the orchestrate agent itself — observable in principle.

4. **`per_agent_tokens` awk pass** in `compute-swe-metrics.sh` (lines 397–429) keys on `agent:` field. Inline steps that carry `agent: inline` would appear in per-agent aggregation as a single `inline` bucket — acceptable, but the architect should decide whether to use a synthetic `agent: inline` marker or a separate `per_inline_steps` block.

5. **`run-ux-critique` position** in `feature.yaml` implement phase (line ~163): `run-ux-critique if ux_design` sits between `run-simplify` and `run-phase-review`. This feature's `ux_design: false` filters it out, but a solution must account for this step when designing the collapsed implement-phase review sequence.

6. **`metrics-schema.md` consumer contract** must be updated to add `per_step` to the field registry and mark it R (required) across all schemas, since per-step data exists regardless of schema type.

### Build-or-Reuse Assessment

All three sub-problems are "extend existing" changes. No external library exists for step-ordering fixes in a YAML-driven workflow engine. The metrics script, step contracts, and dispatch skill are all bespoke config — the work is modifying those files, not adopting a new dependency. Specifically:

- Issue 1 (ordering): reorder two lines in `_complete-phase.yaml` and split one step into two
- Issue 2 (per-step metrics): extend the existing awk pass in `compute-swe-metrics.sh` and extend the dispatch loop's existing usage-recording block
- Issue 3 (reviewer spawns): replace three step entries in `feature.yaml` with two, and write one new step contract that combines the scope of the three
