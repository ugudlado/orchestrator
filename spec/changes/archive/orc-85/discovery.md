---
feature-id: orc-85
linear-ticket: ORC-85
---

# Discovery Brief: Dispatch retry storm — model=none spawn failures bypass retry limit and state-transition guard

## Feature Summary

During `orc-84`, the dispatch loop produced 14 consecutive failure entries for `task-T-1`
with `usage.model: "none"` and `cost_usd: 0.0`, interleaved with two genuine completions
(attempts 11 and 15). Two bugs collapse into one observable symptom: (1) when the agent
spawn fails *before* the tool emits any output (a "pre-agent" failure), the driver records
a `failed` step_history entry with synthetic `_EMPTY_USAGE` and immediately re-dispatches
the same step — there is no per-step attempt cap enforced anywhere in the driver loop or
the dispatcher; (2) a successfully recorded `completed` entry (attempt 11) did not stop
the loop from re-dispatching `task-T-1`, so a terminal completion failed to act as the
loop-terminating signal it should be. Fixing both is required so a stuck tool binary
cannot wedge a feature into runaway retries and so a recorded completion is a hard exit
condition for any task-node.

## Personas & Actors

- **Driver** — `scripts/run-workflow.sh` shell loop that calls `orchestrator next` and
  spawns agent/script subprocesses.
- **Dispatcher** — `config/scripts/orchestrator_next/dispatch.py` (`dispatch()` function)
  that selects the next ready node via `readiness.next_ready_node`.
- **Recorder** — `config/scripts/orchestrator_next/record.py` (`orchestrator done` verb)
  that appends step_history entries and mutates `workflow_plan[phase].nodes[*].status`.
- **Tool binary** — `claude`, `cursor`, or `pi` CLI; invoked via `invoke_tool` in
  `run-workflow.sh`. May exit non-zero before emitting a COMPLETION block (auth glitch,
  rate-limit, transient crash).
- **Operator** — the human watching the loop; today receives no halt signal when a
  spawn-failure storm begins, only an ever-growing `step_history`.

## Use Cases

### Happy Path

UC-1: spawn-failure cap — driver wants to halt after N pre-agent spawn failures for a
single task-node so that a stuck tool binary cannot burn arbitrary wall-clock and DB
rows. The dispatcher (or the driver) MUST exit 2 ("blocked") on the (N+1)th
zero-token failure for the same `(phase, step_id)` pair.

UC-2: terminal completion is sticky — once `step_history` has a `completed` entry for a
task-node `task-T-N`, the dispatcher MUST never return `task-T-N` from
`next_ready_node` / `repeat_until_redispatch` again. A subsequent `orchestrator next`
call MUST either advance to the next node, exit 1 (phase complete), or exit 2
(blocked) — never re-emit a completed task-node.

UC-3: real failures still retry — a genuine agent failure (tokens > 0, model resolved,
exit non-zero after partial work) should still count against the retry budget so a
flaky agent can recover within the cap, distinguishing "the spawn never produced
billable work" from "the agent ran and failed".

### Error & Edge Cases

UC-E1: spawn fails repeatedly with `model: none` — today: 14 retries, no halt, no
operator signal. After fix: cap (e.g. ≤ `quality_bar.max_retry_rounds`, default 3 or 8
from `spec/project.yaml`) is enforced; on overflow the driver exits 2 with a clear
"spawn loop aborted: N consecutive zero-token failures for <step_id>" message.

UC-E2: record fails to write `ended_at` on completion — observed in orc-84 attempt 11
(status=completed but `ended_at` field missing). Whatever ended the partial write must
not cause the node to be re-considered ready. The terminal-completion check (UC-2)
MUST short-circuit on `step_history` regardless of node-status drift, otherwise a
record-time crash silently re-arms re-dispatch.

UC-E3: mixed storm + completion — orc-84 attempt 11 completed but attempts 12-14 still
re-ran (model=none) before attempt 15 succeeded. Both bugs co-occur. The test
fixture must reproduce this exact sequence (multiple model=none failures, then a
completed entry, then more model=none failures) and assert that no further dispatch
of the completed step_id occurs.

## Scope

### In Scope

- Dispatcher and/or driver logic that prevents pre-agent spawn failures (model=none,
  zero tokens, no COMPLETION block parsed) from incrementing the retry counter that
  is checked against `quality_bar.max_retry_rounds`, OR that adds a separate
  spawn-failure cap distinct from the agent-retry cap.
- Dispatcher logic that treats any `step_history` entry with
  `status ∈ {completed, recovered}` for a `(phase, step_id)` task-node as a hard
  terminator: `next_ready_node` / `repeat_until_redispatch` MUST NOT return that
  step_id again, even if `workflow_plan[phase].nodes[*].status` is stale.
- A bats or pytest test that reproduces both bugs from a synthetic state.yaml fixture
  and asserts the new behavior.
- Surfacing the spawn-failure cap exhaustion to the operator (driver stderr + exit
  code 2).

### Out of Scope

- Fixing the root cause of pre-agent spawn failures (auth glitches, transient
  `claude`/`cursor` binary crashes). Rationale: ORC-85 is a defense-in-depth fix for
  the dispatcher, not a tool-binary reliability story.
- Reworking `_compute_attempt` semantics globally. Rationale: keep the change
  minimally invasive; gate the new behavior on the model=none / no-COMPLETION-parsed
  signal that distinguishes pre-agent failures from real agent failures.
- Reworking `repeat_until_redispatch` for non-task-node steps (e.g. run-phase-review
  rework loop). Rationale: rework loop has its own cap via
  `_rework_loop_active`; ORC-85 is about task-nodes, not phase-review nodes.
- Backfilling historical retry-storm rows in `metrics.duckdb`. Rationale: archive-only
  data, no operational impact.

## UI Direction

N/A — no UI components. This is a CLI / dispatcher behavior change.

## Key Decisions

- **Selected design**: Approach A — Dispatcher-side spawn-failure cap (new
  `_consecutive_spawn_failures` guard in `dispatch.py`) plus making
  `readiness._effective_node_status` history-authoritative for all plans (not
  only legacy `active:[ids]`). Complexity: M.
- **Spawn-failure signal (resolves OQ-2)**: exact shape
  `usage.model == "none"` AND `input_tokens == 0` AND `output_tokens == 0`.
  Reuses what `state_inspect build-payload failed` already writes; no
  TOOL_EXIT coupling.
- **Cap location (resolves OQ-1)**: dispatcher, not driver. UC-2
  (completion stickiness) is a dispatcher invariant and must live there;
  co-locating both fixes keeps the bugfix coherent.
- **Cap value (resolves OQ-3)**: separate `quality_bar.max_spawn_failures`,
  default 3. Pre-agent failures are free; tighter cap surfaces the problem
  faster than reusing `max_retry_rounds: 8`.
- **Completion-stickiness mechanism (resolves OQ-4)**: extend
  `_effective_node_status` to consult step_history for all plans. Renders
  the orc-84 partial-write hazard non-fatal without scoping in a record.py
  fix.
- **Test strategy (resolves OQ-5)**: pytest covers dispatcher invariants
  (AC-1 through AC-4); one bats smoke test covers the driver-level
  exit-2 + stderr requirement (AC-6).

## Open Questions

- OQ-1: Where to enforce the spawn-failure cap — in `dispatch.py`
  (`_compute_attempt` or a new `_consecutive_spawn_failures` guard returning exit 2),
  or in `run-workflow.sh` (count consecutive `TOOL_EXIT != 0` events for the same
  `STEP_ID` and break the loop)? The dispatcher path is more testable and survives
  alternate drivers; the driver path is closer to the actual spawn boundary and can
  surface tool stderr more naturally. Design step should pick one.
- OQ-2: What counts as a "pre-agent spawn failure" for the cap?
  Candidate signals: (a) `usage.model == "none"` AND
  `input_tokens == 0` AND `output_tokens == 0`, OR (b) the parse-completion.py path
  was never reached (`TOOL_EXIT != 0` in run-workflow.sh line 601-612), OR (c) both.
  Note: `state_inspect.py` already defines `_EMPTY_USAGE` and `_usage_has_tokens` —
  reuse those primitives rather than re-deriving.
- OQ-3: Should the spawn-failure cap be the same number as
  `quality_bar.max_retry_rounds` (8 in this repo) or a separate, tighter cap
  (e.g. 3)? A pre-agent failure consumes ~0s and $0; eight retries is cheap but the
  current symptom shows it produces operational noise without diagnostic value. A
  separate `quality_bar.max_spawn_failures: 3` may be clearer.
- OQ-4: For the completion-stickiness fix, should `next_ready_node` look at
  `step_history` directly (more robust against node-status drift, slower) or trust
  `workflow_plan[phase].nodes[*].status` (already the design intent, faster) and fix
  the partial-write hazard in `record.py` separately? The orc-84 evidence (attempt 11
  completed but missing `ended_at`) hints that `mark_node_status` may not always run
  to completion — investigate whether the post-write YAML parse guard
  (`record.py:1654-1673`) restored a pre-write copy that lost the node-status flip.
- OQ-5: Test fixture strategy — a pytest unit test against `dispatch()` with a
  synthetic state.yaml is straightforward; a bats end-to-end test requires stubbing
  `invoke_tool` to return non-zero N times then a valid COMPLETION. Pick one
  (probably both, with the pytest covering the dispatcher invariant and a single
  bats smoke test covering the driver-level halt).
