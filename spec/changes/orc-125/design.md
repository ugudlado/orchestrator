---
feature-id: orc-125
linear-ticket: ORC-125
---

# Design: Restore mid-run live cost display

## Context

ORC-42 (archived `live-cost-probe`) attached a running cost total to the
`orchestrator next` action dict under `estimated_cost_so_far` and printed
`[cost so far: $X.XX]` after each step. The value was produced by `sum_cost_usd(db, ...)`
in `upsert.py`, a DuckDB `SELECT SUM(gen_ai_usage_cost_usd)`. The DuckDB removal
(commit 9b2a606, 2026-06-01) deleted `upsert.py`/`sum_cost_usd()` and the action-dict
field with it; nothing replaced the display. A grep of `bin/orchestrator` and
`orchestrator_next/*.py` (non-test) for `cost_so_far` / `sum_cost_usd` / `estimated_cost`
returns zero hits — a confirmed regression.

The data the probe needed still exists, in a different store: `record.py._build_history_entry`
computes each step's cost via `pricing._compute_cost_usd` and writes it to
`step_history[-1].usage.cost_usd` at record time. The `workflow-report` step already sums
`usage.cost_usd` across `step_history` at end-of-run
(`config/steps/workflow-report/workflow_report_step.py::_render_report`, `cost = usage.get("cost_usd") or 0.0`;
`total_cost += cost`). This design re-derives the running total from that same field, mid-run,
without any DB.

### Verified System Boundaries

Claims below were grep-verified against HEAD before finalizing:

- `record.py:493-497` writes `usage["cost_usd"]` into the `step_history` entry at record
  time (`if not usage.get("cost_usd"): ... usage["cost_usd"] = computed_cost`). Source of
  truth confirmed — per-step cost is present in state.yaml the instant a step is recorded
  (satisfies mid-run).
- `bin/orchestrator:352,382` — the `next` verb calls `action, exit_code = dispatch(state, ...)`
  then `print(emit_json(action), end="")`. `dispatch()` receives the full `state` (which
  carries `state.step_history` and `state.raw`). The action dict is the injection point for
  `estimated_cost_so_far`, mirroring ORC-42.
- `dispatch.py::dispatch` builds the action via `_build_action_base` / `_dispatch_fresh` /
  `_handle_resume`; all have `state` in scope, so the field can be added on the fresh and
  resume return paths.
- `run_loop.py:515` — `_log(f"✓ {action['step_id']}  done  status=...")` is the local
  self-drive completion log; `state = load_state(state_yaml_path)` is available (line 481)
  and `record()` has just written the new cost to disk, so re-reading `step_history`
  post-record yields the up-to-date total.
- `workflow_report_step.py:77` — existing summation precedent: `usage.get("cost_usd") or 0.0`;
  the helper mirrors this null-tolerant read.

## Goals / Non-Goals

### Goals

- Re-derive a running cost total by summing `step_history[].usage.cost_usd` from the
  in-progress `state.yaml`.
- Surface the total to the operator wherever a completed step is reported: the
  `orchestrator next` action dict (`estimated_cost_so_far`, restoring ORC-42's shape) and
  the local `run_loop.py` step-completion log (`[cost so far: $X.XX]`).
- Work mid-run (updates after every step), verified on a multi-step fixture.
- Keep the change agent-agnostic — no edits to `config/workflows/*` or `config/steps/*`.

### Non-Goals

- Do NOT reintroduce DuckDB, `metrics.duckdb`, `upsert.py`, or `sum_cost_usd(db, ...)`.
- Do NOT change how per-step `cost_usd` is computed or stored (`pricing.py`/`record.py`
  untouched as the source of truth; only a new read-only helper is added to `pricing.py`).
- Do NOT add a new CLI subcommand (e.g. `orchestrator cost`).
- Do NOT aggregate across sibling design/implement/review state files — the meter is
  per-run; `workflow-report` owns cross-file roll-up.

## Approaches Considered

### Approach 1: Helper in `pricing.py` + action-dict field + run_loop log

Add a pure `sum_cost_usd(step_history) -> float` to `orchestrator_next/pricing.py`
(co-located with the existing `_compute_cost_usd`), that sums `usage.cost_usd` over
`step_history`, null/malformed-tolerant. Inject `estimated_cost_so_far` into the action
dict inside `dispatch()` (so `orchestrator next` emits it — the remote/DRIVE.md +
orchestrate consumers read it). In `run_loop.py`, after `record()`, re-load state and print
`[cost so far: $X.XX]` alongside the `✓ ... done` line (suppressed when the total is `0.0`).

- Pros: reuses the existing pricing module and the ORC-42 `estimated_cost_so_far` field
  shape; covers both surfaces named by AC-1; summation logic lives in one testable pure
  function; no DB.
- Cons: touches three files (pricing.py, dispatch.py, run_loop.py).
- Complexity: **S**. Module reuse: high (co-locates in `pricing.py`; restores the ORC-42
  action-field precedent).

### Approach 2: New standalone `cost.py` module wired into both surfaces

New `orchestrator_next/cost.py` holding the summation + a `$X.XX` formatter, imported by
`dispatch.py` and `run_loop.py`.

- Pros: clean separation; a home for future cost-display helpers.
- Cons: a whole new module + import surface for a single-function concern that already has a
  natural home next to `_compute_cost_usd` in `pricing.py`; lower reuse of existing structure.
- Complexity: **M**.

### Approach 3: run_loop-only inline sum (no action-dict field)

Compute the sum inline in `run_loop.py` and print the meter only in the local completion
log; do not restore the `orchestrator next` action-dict field.

- Pros: smallest touch (one file); simplest.
- Cons: leaves the remote/DRIVE.md + orchestrate path (which drives `next`/`done` and reads
  the action dict) without the meter — the exact interactive design-review loop the
  `specify-phase-scope-churn-cost` learning targets. Duplicates the summation inline rather
  than in a reusable, unit-tested helper.
- Complexity: **S**. Module reuse: low.

### Selected Approach

**Approach 1.** Auto-selection heuristic: map complexity (Approach 1 = S = 2, Approach 2 =
M = 3, Approach 3 = S = 2); lowest numeric wins → tie between Approaches 1 and 3 at S.
Tie-break rule (c): prefer higher module reuse count. Approach 1 reuses the existing
`pricing.py` module (co-located with `_compute_cost_usd`) and restores the existing ORC-42
`estimated_cost_so_far` action-field consumer contract, covering both the local and remote
surfaces; Approach 3 duplicates summation inline and reuses nothing, covering only one
surface. Approach 1 has the higher reuse count → selected. Approach 2 is ruled out by the
complexity constraint (M > S) — a new module is unjustified for a single pure function that
`pricing.py` already has the natural home for.

## High-Level Design

### Architecture Overview

```
record.py  ──writes──▶  state.yaml (step_history[].usage.cost_usd)   [unchanged source of truth]
                                    │
                    ┌───────────────┴────────────────┐
                    ▼                                 ▼
   pricing.sum_cost_usd(step_history)     pricing.sum_cost_usd(step_history)
                    │                                 │
                    ▼                                 ▼
   dispatch(): action["estimated_        run_loop: [cost so far: $X.XX]
   cost_so_far"] = total                 printed after "✓ <step> done"
   (orchestrator next → remote/           (local self-drive CLI surface)
    orchestrate consumers)
```

A single pure summation function feeds two independent display sites. No new store, no new
subcommand, no schema/step change.

### Key Abstractions

- `sum_cost_usd(step_history: list) -> float` in `orchestrator_next/pricing.py`: the one
  new abstraction. Read-only, side-effect-free, tolerant of `None`/missing `usage` and
  non-dict entries (mirrors `workflow_report_step._render_report`'s `usage.get("cost_usd") or 0.0`).
- `estimated_cost_so_far` action-dict key: the restored ORC-42 contract for the
  `orchestrator next` JSON, consumed by the remote/DRIVE.md driver and the orchestrate skill.

## Low-Level Design

### Components

1. **`orchestrator_next/pricing.py`** — add `sum_cost_usd(step_history) -> float`. Iterate
   entries; for each dict entry read `usage = entry.get("usage")`; if `usage` is a dict, add
   `float(usage.get("cost_usd") or 0.0)`; skip non-dict entries/usages. Return the running
   total (`0.0` when empty). No I/O — the caller supplies `step_history`.
2. **`orchestrator_next/dispatch.py`** — in `dispatch()`, on the exit-0 action paths (fresh
   and resume), set `action["estimated_cost_so_far"] = sum_cost_usd(state.step_history)`.
   `state.step_history` is a list of `StepHistoryEntry`; the helper reads `.usage` — accept
   both dict entries and `StepHistoryEntry` (read `getattr`/`.usage` uniformly, or normalize
   via `entry.usage`). Keep `emit_json` deterministic (sorted keys) — the new key sorts in
   naturally.
3. **`orchestrator_next/run_loop.py`** — after the successful `record()` at ~line 509-515,
   re-load state (`load_state`) or read the returned state, compute
   `total = sum_cost_usd(step_history)`, and if `total > 0` emit
   `_log(f"  [cost so far: ${total:.2f}]")` immediately after the `✓ ... done` line. When
   `total == 0.0`, suppress (OQ-2 resolution) to avoid an always-`$0.00` line on token-less
   cloud runs.
4. **Tests** — `orchestrator_next/tests/test_cost_so_far.py`: unit tests for
   `sum_cost_usd` (two entries 0.01 + 0.02 → 0.03; empty → 0.0; null/missing `cost_usd`
   ignored; non-dict entry ignored) and an integration assertion that `orchestrator next`
   against a multi-step fixture emits `"estimated_cost_so_far"` with the summed float.

### Data Flow

`record()` writes `usage.cost_usd` per step → on the next `orchestrator next`, `dispatch()`
reads `state.step_history` and injects `estimated_cost_so_far` into the emitted action JSON →
in the local loop, `run_loop` reads the just-recorded `step_history` and prints the meter.
Both reads are of the same in-progress `state.yaml`.

### State Management

No new persistent state. `step_history[].usage.cost_usd` (written by `record.py`) is the
only state read. The helper is read-only; nothing is written back.

### Error Handling

- Empty / absent `step_history` → `0.0`.
- Entry not a dict / `usage` not a dict / `cost_usd` null or absent → that entry contributes
  `0.0`; summation never raises (mirrors the existing `workflow-report` null-tolerant read).
- The action-dict field and the log line are additive; a `0.0`/degraded total never blocks
  dispatch or the completion report.

## Constraints

- No DuckDB / `metrics.duckdb` (ticket AC-2; supersedes `metrics-db-derived`).
- Agent-agnostic: no changes to `config/workflows/*` or `config/steps/*` (project rule
  `agent-agnostic`).
- `emit_json` output must stay deterministic (sorted keys) — the added key must not break
  byte-stable JSON tests; a fixture/expectation update is in scope if any test pins the exact
  action JSON.
- verify commands repo-root-relative (project convention).

## Trade-offs

- Two display sites (action dict + run_loop log) instead of one adds a small amount of
  duplication at the call sites, accepted because the summation itself is a single shared
  pure function and the two surfaces serve different drivers (remote vs local). The
  alternative (Approach 3, one site) would leave the interactive design-review loop — the
  precise `specify-phase-scope-churn-cost` leverage point — without a meter.
- Recomputing the sum on every `next` is O(steps); step counts are tiny (single-digit to low
  double-digit), so no caching is warranted.

## Acceptance Criteria

- AC-1: Given a `state.yaml` whose `step_history` has two completed entries with
  `usage.cost_usd` of 0.01 and 0.02, when `orchestrator next <state>` is run, then the
  emitted action JSON contains `"estimated_cost_so_far": 0.03`. [traces: UC-1]
- AC-2: Given a fresh `state.yaml` with no completed steps (or all `cost_usd` null/absent),
  when `orchestrator next` runs, then `estimated_cost_so_far` is `0.0` and dispatch does not
  raise. [traces: UC-E1, UC-E2]
- AC-3: Given a multi-step local run via the in-process loop, when each step is recorded,
  then a `[cost so far: $X.XX]` line reflecting the summed `step_history[].usage.cost_usd`
  is printed after the `✓ <step> done` line for steps where the running total > 0 — proving
  the display works mid-run, not only at complete time. [traces: UC-1, UC-2]
- AC-4: Given the whole change, when `git diff` is inspected, then no file under
  `config/workflows/` or `config/steps/` is modified and no DuckDB/`metrics.duckdb`/
  `upsert.py`/`sum_cost_usd(db, ...)` symbol is (re)introduced. [traces: UC-1]
  (verify: `git diff --name-only main...HEAD` lists no `config/workflows/` or `config/steps/`
  path; `grep -rn "duckdb\|metrics.duckdb" orchestrator_next bin` returns no new hits.)
- AC-5: Given `sum_cost_usd`, when it receives malformed `step_history` (non-dict entry, or
  entry whose `usage` is not a dict), then it skips that entry and returns the sum of the
  well-formed ones without raising. [traces: UC-E2]

## Decisions

- Put the helper in `pricing.py`, not a new module → the only existing cost-logic home
  (`_compute_cost_usd` lives there) → higher reuse, tie-break winner, no new import surface.
- Restore the `estimated_cost_so_far` action-dict key (ORC-42's exact name) rather than
  invent a new one → preserves the remote/DRIVE.md + orchestrate consumer contract → those
  paths get the meter for free.
- Suppress the run_loop meter when total == 0.0 (OQ-2) → avoids an always-`$0.00` line on
  token-less `ORCHESTRATOR_SKIP_USAGE_CHECK` cloud runs → the action-dict field still always
  carries a numeric `0.0` for machine consumers.

## Open Questions

- None blocking. OQ-1 (which surface) and OQ-2 (zero-cost presentation) from discovery.md are
  resolved in Decisions above.
