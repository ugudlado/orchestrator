---
feature-id: orc-85
linear-ticket: ORC-85
---

# Design: Dispatch retry storm — bound spawn failures and make completion sticky

## Context

The dispatcher (`config/scripts/orchestrator_next/dispatch.py`) selects the next
ready task-node via `readiness.next_ready_node`. The driver
(`scripts/run-workflow.sh`) spawns the chosen agent and, on tool exit ≠ 0
before any COMPLETION block is parsed, records a synthetic `failed` step_history
entry with `usage = {input_tokens: 0, output_tokens: 0, model: "none"}` (see
`scripts/lib/state_inspect.py:163-188`). Today there is no per-task cap on how
many of these pre-agent failures the loop will absorb: `_compute_attempt`
(`dispatch.py:40-53`) just monotonically increments the attempt number, and
`quality_bar.max_retry_rounds` is enforced only inside the run-phase-review
rework loop (`record.py:_max_retry_rounds`, `_rework_loop_active`). The
node-readiness check, in turn, reads `_effective_node_status`
(`readiness.py:83-91`), which only falls back to step_history when the phase
uses a **legacy** `active:[ids]` plan — promoted node plans rely on
`workflow_plan[phase].nodes[*].status` alone, so if `mark_node_status` does not
persist (e.g. the post-write YAML parse restored a pre-write copy,
`record.py:1654-1673`), a completed task-node can be re-dispatched.

orc-84 hit both: 14 consecutive `model=none` failures for `task-T-1`, one
completion at attempt 11 whose node-status flip did not survive, then more
retries until attempt 15 finally won.

## Goals / Non-Goals

### Goals

- Bound consecutive pre-agent spawn failures per `(phase, step_id)` to a small
  configurable cap; on overflow, the dispatcher returns exit 2 ("blocked") with
  a clear message and the driver halts.
- Make a `completed`/`recovered` step_history entry an absolute terminator for
  the matching task-node: `next_ready_node` MUST NOT return that node again,
  regardless of `workflow_plan[phase].nodes[*].status`.
- Distinguish pre-agent spawn failures (no billable work) from genuine agent
  failures (tokens > 0, model resolved) so the latter still consume the
  ordinary retry budget.
- Cover the new behavior with a pytest suite against `dispatch()` and
  `readiness` plus one bats smoke test exercising the driver-level halt path
  (stderr + exit 2).

### Non-Goals

- Fixing the underlying causes of pre-agent spawn failures (tool-binary
  crashes, auth glitches, rate limits).
- Reworking `_compute_attempt` semantics for non-spawn-failure cases.
- Reworking the run-phase-review rework loop (`_rework_loop_active`) — its cap
  already works.
- Backfilling historical retry-storm rows in `metrics.duckdb`.
- Fixing the partial-write hazard in `record.py:1654-1673` directly. The
  completion-stickiness fix (history-authoritative `_effective_node_status`)
  renders the hazard non-fatal; a dedicated record-side fix is out of scope.

## Approaches Considered

### Approach A: Dispatcher-side cap + history-authoritative completion (selected)

Add a `_consecutive_spawn_failures(state, phase, step_id)` helper in
`dispatch.py` that walks `step_history` in reverse and counts the trailing run
of `status == "failed"` entries with `usage.input_tokens == 0`,
`usage.output_tokens == 0`, and `usage.model == "none"` for the matching
`(phase, step_id)`, stopping at the first entry that breaks the run (any other
status, any non-zero usage, or a different step_id). The guard fires inside
`dispatch()` after `next_ready_node` returns a step_id but before the action
response is emitted: if the trailing-failure count is `≥ quality_bar
.max_spawn_failures` (default 3, new key) and the just-selected step is the
same `step_id`, return exit 2 with a `blocked: spawn_failure_cap` reason.

For UC-2, extend `_effective_node_status` in `readiness.py` to consult
step_history for **all** plans, not only legacy `active:[ids]` plans: if the
node's plan status is not `completed` but step_history has a terminal
`completed`/`recovered` entry for the matching `(phase, node_id)`, treat the
node as `completed`.

- **Pros**: single dispatcher-layer change, testable purely in Python with
  synthetic state.yaml fixtures, survives alternate drivers, fixes both bugs
  symmetrically.
- **Cons**: tightens semantics of `_effective_node_status` for promoted plans
  — a hand-edit that un-sets a node to `pending` after a completed history
  entry is no longer respected. Not a supported workflow, so the loss is
  acceptable.
- **Complexity**: M.

### Approach B: Driver-side counter in `run-workflow.sh`

Track a per-`STEP_ID` consecutive zero-token failure counter inside
`run-workflow.sh`; break the dispatch loop with exit 2 once it exceeds the
cap.

- **Pros**: closest to the spawn boundary; easy to surface tool stderr.
- **Cons**: shell-only state is fragile; cannot fix UC-2 (completion
  stickiness is a dispatcher invariant); test by bats only; alternate drivers
  (e.g. future Python driver) would re-introduce the bug.
- **Complexity**: S.

### Approach C: Approach A plus record.py post-write read-back

Add Approach A and additionally, in `record.py`, after the post-write YAML
parse guard, re-apply `mark_node_status(state_raw, phase, step_id, "completed")`
if step_history shows completion but the on-disk node is still pending — a
belt-and-suspenders fix for the partial-write hazard.

- **Pros**: also closes the partial-write hazard at the source.
- **Cons**: larger surface area; the partial-write hazard is a separate bug
  with its own scope; history-authoritative `_effective_node_status` already
  makes the hazard non-fatal.
- **Complexity**: L.

### Selected Approach

**Approach A.** B is structurally incapable of fixing UC-2 (completion
stickiness must live in the dispatcher), so it is eliminated, not
deprioritized. C is correct but exceeds the discovery scope, which explicitly
defers the partial-write hazard root cause. A delivers both UC-1 and UC-2 in
one dispatcher-layer change, mirrors the existing `_rework_loop_active` and
`_effective_node_status` patterns, and is fully unit-testable against
synthetic state.yaml fixtures.

## High-Level Design

### Architecture Overview

```
                          orchestrator next  ─►  dispatch.dispatch()
                                                        │
                                                        ├── readiness.next_ready_node
                                                        │       (now history-authoritative
                                                        │        for promoted plans too)
                                                        │
                                                        ├── _consecutive_spawn_failures
                                                        │       (NEW guard, in dispatch.py)
                                                        │
                                                        └─► action  /  exit 2 blocked
                                                                        ▲
                                                                        └── operator sees
                                                                            "spawn loop aborted"
```

`run-workflow.sh` is unchanged on the happy path; on the new exit-2 branch it
prints the dispatcher's stderr message and propagates the exit code, which it
already does for any non-zero return from `orchestrator next`.

### Key Abstractions

- `_consecutive_spawn_failures(state, phase, step_id) -> int` — pure function
  in `dispatch.py`. Walks `state.step_history` in reverse, stops at the first
  entry that breaks the trailing-zero-token-failure run for `(phase, step_id)`,
  returns the run length.
- `_max_spawn_failures(state_raw) -> int` — sibling of `_max_retry_rounds` in
  `dispatch.py` (NOT in record.py — dispatch is the consumer). Reads
  `quality_bar.max_spawn_failures` from `spec/project.yaml` via the existing
  project.yaml resolution path; default 3.
- `_effective_node_status` (existing, in `readiness.py`) — extended: the
  legacy-only step_history fallback becomes unconditional.

## Low-Level Design

### Components

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/dispatch.py` | Add `_consecutive_spawn_failures` and `_max_spawn_failures`; call the guard inside `dispatch()` after `next_step_id` is resolved but before building the action response. On overflow, return a `blocked` action with `reason: "spawn_failure_cap"` and exit code 2. |
| `config/scripts/orchestrator_next/readiness.py` | `_effective_node_status`: drop the `_uses_legacy_active_plan(state)` gate from the step_history-completion branch so step_history is authoritative for all plans. |
| `spec/project.yaml` | Add `quality_bar.max_spawn_failures: 3` (the orchestrator repo's own config; other repos pick up the dispatcher default). |
| `config/scripts/orchestrator_next/tests/test_dispatch_retry_storm.py` (NEW) | pytest suite covering UC-1, UC-2, UC-3, UC-E1, UC-E3 against synthetic State fixtures. |
| `tests/bats/spawn_failure_halt.bats` (NEW) | bats smoke test: stub `orchestrator next` (or `claude`) to return non-zero N times, assert the driver exits 2 and prints the cap-exhaustion message. |

### Data Flow

1. Driver calls `orchestrator next $STATE_YAML`.
2. `dispatch()` calls `readiness.next_ready_node(state)`. For promoted plans,
   a node whose `step_history` shows `completed`/`recovered` for the matching
   `(phase, node_id)` is now skipped via the extended `_effective_node_status`
   — regardless of `workflow_plan[phase].nodes[*].status` drift. (UC-2, UC-E2.)
3. If a step_id is returned, `dispatch()` invokes
   `_consecutive_spawn_failures(state, phase, step_id)`. If the count ≥
   `_max_spawn_failures(state.raw)`, `dispatch()` emits a `blocked` action and
   returns exit 2.
4. Driver propagates exit 2; loop ends. Operator sees:
   `BLOCKED: spawn_failure_cap — <N> consecutive zero-token failures for <phase>/<step_id>`.

### State Management

No new persistent state. The cap is read from `spec/project.yaml` at each
`orchestrator next` invocation, mirroring `_max_retry_rounds`. The
trailing-failure run is recomputed from `step_history` each call — same model
as `_compute_attempt`.

### Error Handling

- Missing `quality_bar.max_spawn_failures` in project.yaml → default 3
  (mirrors the `_max_retry_rounds` fallback at `record.py:118`).
- Malformed `usage` block in a step_history entry (no `model`, missing
  token fields) → treated as **not** a spawn failure (conservative; only the
  exact `{input_tokens:0, output_tokens:0, model:"none"}` shape counts).
- step_history with the failure run interrupted by a non-matching
  `(phase, step_id)` → count restarts; only **consecutive** failures for the
  **same** task-node trigger the cap.

## Constraints

- Must not change `_compute_attempt` semantics — many existing tests pin them.
- Must not break the run-phase-review rework loop (`_rework_loop_active`).
- Default `max_spawn_failures` must be small (3) — a pre-agent failure is
  ~0s/$0, so 3 retries is generous without producing operational noise.

## Trade-offs

- Tighter `_effective_node_status` semantics for promoted plans: a manual
  reset of `nodes[*].status` after a completed history entry no longer
  re-arms re-dispatch. The discovery brief calls this out as the desired
  behavior; the only loss is hand-edit "uncomplete" which isn't supported.
- Separate cap (`max_spawn_failures`) rather than reusing `max_retry_rounds`:
  one more config knob, but reusing the rework-loop cap (default 8) would
  permit 8 free model=none storms per task — the exact symptom we're
  preventing.

## Acceptance Criteria

- AC-1: When `step_history` contains ≥ `quality_bar.max_spawn_failures`
  (default 3) **consecutive** entries with `status == "failed"`,
  `usage.input_tokens == 0`, `usage.output_tokens == 0`, and
  `usage.model == "none"` for the same `(phase, step_id)`, and the next
  ready node selected by `next_ready_node` is the same `step_id`,
  `orchestrator next` exits 2 with stderr containing
  `spawn_failure_cap` and the failing step_id. [traces: UC-1, UC-E1]

- AC-2: When `step_history` contains a `status ∈ {completed, recovered}`
  entry for `(phase, step_id)`, `readiness.next_ready_node` MUST NOT
  return that step_id again, even when
  `workflow_plan[phase].nodes[*].status` for that node is still `pending`
  or `in_progress`. This holds for both promoted `nodes:` plans and
  legacy `active:[ids]` plans. [traces: UC-2, UC-E2]

- AC-3: A `failed` step_history entry with `usage.input_tokens > 0`
  **OR** `usage.output_tokens > 0` **OR** `usage.model != "none"` is
  **not** counted toward the spawn-failure cap — the dispatcher returns a
  normal `action` response and the driver re-dispatches as before.
  [traces: UC-3]

- AC-4: Given the orc-84 fixture (step_history with multiple
  model=none failures, one `completed` entry for the same step_id, then
  more model=none failures), `next_ready_node` returns a step_id
  **different from** the completed one (or `None` if no work remains).
  [traces: UC-E3]

- AC-5: A pytest module
  `config/scripts/orchestrator_next/tests/test_dispatch_retry_storm.py`
  exercises AC-1 through AC-4 and runs green under
  `cd config/scripts && pytest orchestrator_next/tests/test_dispatch_retry_storm.py -v`.
  [traces: UC-1, UC-2, UC-3, UC-E1, UC-E3]

- AC-6: A bats test `tests/bats/spawn_failure_halt.bats` stubs the tool
  binary to return exit 1 with empty stdout, runs the dispatch loop, and
  asserts (a) the driver exits 2, (b) stderr contains
  `spawn_failure_cap`. [traces: UC-E1]

## Decisions

- Spawn-failure signal: `usage.model == "none"` AND `input_tokens == 0`
  AND `output_tokens == 0` → Reuses the exact shape written by
  `state_inspect.build-payload failed` and is observable in step_history;
  no coupling to TOOL_EXIT → Test fixtures are pure data.
- Cap location: dispatcher, not driver → UC-2 (completion stickiness) is a
  dispatcher invariant; co-locating both fixes keeps the bugfix coherent
  → One Python module under test instead of bats + python split.
- Cap value: separate `quality_bar.max_spawn_failures: 3` rather than
  reusing `max_retry_rounds: 8` → Pre-agent failures are free, 8 retries
  is operational noise not signal → Tighter default surfaces the problem
  to the operator faster.
- `_effective_node_status` becomes history-authoritative for all plans →
  Renders the orc-84 partial-write hazard non-fatal without scoping in a
  record.py fix → Hand-edit "uncomplete" no longer respected (not a
  supported workflow).
- bats coverage limited to driver-level halt path → Dispatcher invariants
  are tested in pytest; only the UC-E1 operator-signal requirement needs
  end-to-end verification.

## Open Questions

(No blockers; OQ-1 through OQ-5 from discovery.md are resolved in
Decisions above.)
