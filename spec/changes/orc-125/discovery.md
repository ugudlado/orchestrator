---
feature-id: orc-125
linear-ticket: ORC-125
---

# Discovery Brief: Restore mid-run live cost display

## Feature Summary

ORC-42 (archived as `live-cost-probe`) once printed a running `[cost so far: $X.XX]`
after every completed step, giving the operator a live cost meter during a workflow run.
That probe was backed by a DuckDB query (`sum_cost_usd()` in the now-deleted `upsert.py`),
which was removed wholesale in the DuckDB removal (commit 9b2a606, 2026-06-01); nothing
replaced the display, so the running-cost signal silently regressed to nothing. A live
grep across `bin/orchestrator` and `orchestrator_next/*.py` for `cost_so_far`,
`sum_cost_usd`, and `estimated_cost` returns zero non-test hits today, confirming the
regression. The fix re-derives the running total by summing `step_history[].usage.cost_usd`
straight from the in-progress `state.yaml` — where `orchestrator_next/pricing.py` +
`record.py` already compute and persist each step's `cost_usd` at record time — and
surfaces that total wherever a completed step is reported back to the user, with no DuckDB
dependency and no LLM-tool-specific wiring. Beyond restoring parity, this display is the
leverage point named by the `specify-phase-scope-churn-cost` learning: showing the meter
running during the interactive design/artifact-review loop nudges users to front-load scope
constraints instead of churning expensive architect rounds.

## Personas & Actors

- **Operator / developer** — the human driving a workflow (`orchestrator run`, or a
  cloud/Slack `next`/`done` session). Primary consumer of the live cost figure.
- **run_loop (local self-drive path)** — `orchestrator_next/run_loop.py`, the in-process
  dispatch loop that logs `✓ <step> done ...` after each recorded step. The natural CLI
  surface for the running total in a local run.
- **`orchestrator next` CLI (remote/DRIVE.md path)** — emits the action dict for the next
  step; ORC-42 attached `estimated_cost_so_far` to that dict. A cloud driver / the
  orchestrate skill reads that field to print the meter.
- **`record.py` / `pricing.py`** — system actors that already compute `usage.cost_usd` per
  step and write it into `step_history`. The data source; unchanged by this feature.
- **`workflow-report` step** — existing consumer that already sums `usage.cost_usd` across
  `step_history` at end-of-run; the summation precedent to reuse.

## Use Cases

### Happy Path

UC-1: Live meter after each step — the operator wants to see `[cost so far: $X.XX]` (a total summed from `step_history[].usage.cost_usd` in the current state.yaml) printed each time a step completes so that they can watch the running cost accumulate during a run.
UC-2: Meter during the interactive design loop — the operator running the `design`/`feature` artifact-review loop wants the running total visible after `design-and-draft-artifacts` and `design-review` so that they front-load scope constraints rather than churn costly architect rounds (per `specify-phase-scope-churn-cost`).

### Error & Edge Cases

UC-E1: No cost data yet — when `state.yaml` has no completed steps, or every `usage.cost_usd` is null/absent/zero (e.g. `ORCHESTRATOR_SKIP_USAGE_CHECK` runs that report zero tokens), the total must resolve to `$0.00` (or be suppressed) without raising or breaking the dispatch/report path.
UC-E2: Malformed / missing state — when `step_history` is missing, non-list, or an entry's `usage` is not a dict, the summation must degrade to `0.0` and never abort the step-completion report.

## Scope

### In Scope

- A small reusable helper that sums `step_history[].usage.cost_usd` from an in-progress
  `state.yaml` (or an in-memory state/step_history), returning a float and tolerating
  null/missing/malformed usage entries.
- Surfacing the running total at the point where a completed step is reported to the user:
  the local `run_loop.py` step-completion log line, and/or the `orchestrator next` action
  dict (restoring an `estimated_cost_so_far`-style field for the remote/DRIVE.md +
  orchestrate-skill path).
- Mid-run behavior (updates after every step, not only at archive/complete).
- Unit/integration test coverage proving the total is summed from state.yaml and works
  mid-run on a multi-step fixture.

### Out of Scope

- Reintroducing DuckDB, `metrics.duckdb`, or `upsert.py`/`sum_cost_usd()` in any form —
  explicitly forbidden by the ticket and the superseded `metrics-db-derived` learning.
- Changing how per-step `cost_usd` is computed or stored (`pricing.py` / `record.py` are
  the untouched source of truth).
- New CLI subcommands (e.g. an `orchestrator cost` command) — ORC-42 rejected this as extra
  surface; the running total rides existing output.
- Any tool-specific wiring in `config/workflows/*.yaml` or `config/steps/*` — the
  `agent-agnostic` rule forbids LLM-tool references in schemas/steps.
- Cross-schema roll-up across sibling state files (design/implement/review); the live meter
  is per-run. `workflow-report` already owns cross-file aggregation.

## UI Direction

N/A — no UI components. Output is CLI/console text (a `[cost so far: $X.XX]`-style string)
and/or a numeric field on the `orchestrator next` action JSON.

## Key Decisions

- Reuse over rebuild: the per-step `cost_usd` field already exists in `step_history` and is
  already summed by `workflow-report` — this feature adds a thin summation helper + a
  display call site, not a new data pipeline. Build-or-reuse decision: **reuse** the
  existing state.yaml cost data; **build** only a minimal helper and wire it into the
  existing step-completion output.
- Data source is `state.yaml`, not a DB: satisfies AC-2 (no DuckDB) and AC-3 (mid-run,
  because `record.py` writes each step's `cost_usd` into `step_history` the moment the step
  is recorded).
- Selected design direction (from design-and-draft-artifacts auto-selection, lowest
  complexity S): **Helper-in-pricing + action-dict field + run_loop log.** Add a pure
  `sum_cost_usd(step_history) -> float` to `orchestrator_next/pricing.py` (co-located with
  the existing `_compute_cost_usd`), inject `estimated_cost_so_far` into the `orchestrator
  next` action dict (restoring ORC-42's field shape for the remote/DRIVE.md + orchestrate
  path), and print `[cost so far: $X.XX]` in `run_loop.py`'s local step-completion log. Ties
  with a run_loop-only variant on complexity (both S); broken toward this approach for
  higher reuse — it co-locates in the existing pricing module and restores the ORC-42
  action-field consumers, covering both the local and remote surfaces named by AC-1
  ("orchestrator next or equivalent").
- OQ-1 resolved: target **both** surfaces (action dict + run_loop log) for full parity.
- OQ-2 resolved: suppress the meter when the summed total is exactly `0.0` in the local
  run_loop log (avoids an always-`$0.00` line on token-less cloud runs); the action-dict
  field is always present as a float (default `0.0`) for machine consumers.

## Open Questions

- OQ-1: Surface site — does the fix target (a) only the local `run_loop.py` completion log
  line, (b) only the `orchestrator next` action dict (`estimated_cost_so_far`, matching
  ORC-42's shape) consumed by the remote/DRIVE.md + orchestrate paths, or (c) both? Both is
  the fullest parity restoration; design must pick and justify per the auto-selection
  heuristic and the `agent-agnostic` constraint.
- OQ-2: Zero-cost presentation — when the summed total is `0.0` (e.g. token-less
  `ORCHESTRATOR_SKIP_USAGE_CHECK` cloud runs), should the meter print `$0.00` or be
  suppressed entirely to avoid an always-`$0.00` line in cloud sessions? Affects the UC-E1
  assertion.
