---
feature-id: orc-103
linear-ticket: ORC-103
---

# Design: needs_work rework loop does not re-run phase review after fix task

## Context

The ORC-67 rework loop is supposed to close the quality gate: when `run-phase-review`
returns `needs_work`/`incomplete_phase`, the reviewer agent appends fix tasks to
`tasks.yaml`, invokes `expand-plan` (which injects `task-fix-N` nodes and rewires
`run-phase-review.depends_on` to the last fix node), and `record.py` re-arms the
review node by calling `readiness.mark_node_status(..., "in_progress")`
(`record.py:1688`). The intent: after the fix task completes, the DAG-walk re-emits
`run-phase-review` (attempt N+1) and only advances once the verdict passes.

In the orc-96 run (2026-05-29) the fix task (`task-fix-1`) spawned and committed
correctly, but the workflow then advanced straight to `compute-prediction-accuracy`
— it never re-ran `run-phase-review`. The gate was carried open.

**Root cause (verified against HEAD).** `record.py` re-arms the *node* to
`in_progress`, but the `step_history` entry for the needs_work review is written
with `status: completed` (the review genuinely ran to completion). The DAG-walk's
`readiness._effective_node_status` (`readiness.py:84`) overlays history on the node
status: if `_step_completed_in_history` (`readiness.py:74`) finds any terminal
(`completed`/`recovered`) entry for the node, it reports the node `completed`,
overriding the explicit `in_progress`. So the re-arm is silently undone and the
review's chain-dependent (`compute-prediction-accuracy`) becomes ready.

`git blame` pins the regression to ORC-85 (`f796938`, "history-authoritative
completion"): before it, history-completion inference was guarded by
`_uses_legacy_active_plan(state)` and applied only to legacy `active:[ids]` plans.
ORC-85 dropped that guard to make `next_ready_node` robust to crash-mid-write on
promoted `nodes:` plans (a node that crashed after its history entry was written but
before its plan status flipped). That broadening is correct for crash-resume but
invalidates the ORC-67 re-arm signal.

The secondary mechanism (OQ-2): the `max_retry_rounds` escalate branch in
`_rework_loop_active` (`record.py:315`) is currently dead code — because re-review
never fires, `retries["run-phase-review"]` never climbs past round 1, so escalate is
never reached. Closing the re-emission loop is what makes the existing cap live.

## Goals / Non-Goals

### Goals

- A `needs_work`/`incomplete_phase` verdict reliably re-dispatches `run-phase-review`
  after the spawned fix task-node(s) complete (attempt incremented).
- Advance past the review gate only on a passing verdict
  (`overall >= quality_bar.min_phase_review_score`); no unresolved `needs_work`
  verdict is carried forward.
- Cap re-review rounds at `quality_bar.max_retry_rounds`; on exhaustion, block
  (exit 2, state `paused`) rather than silently advancing.
- Preserve ORC-85's history-authoritative crash-resume behaviour for all other
  step kinds and verdicts.

### Non-Goals

- Making the dispatcher honour an agent-emitted `next_step` field — that fights the
  ORC-63 "DAG-walk is the single source of truth" invariant and is the wrong layer.
  (The ticket's `next_step` framing is the symptom, not the mechanism.)
- Changing the reviewer's fix-task generation or `expand-plan` insertion/rewiring —
  fix-node injection and the `run-phase-review.depends_on → last fix node` edge
  already work (verified in orc-96 and `expand_plan.py:9-10`).
- Adding a new `step_history` status value or otherwise changing the recorded
  `completed` status (it must stay `completed` so metrics and `extract_review_scores`
  are unaffected).
- Changing crash-resume / reconcile semantics beyond distinguishing "completed for
  good" from "completed once, must re-run."

## Approaches Considered

### Approach 1: Verdict-aware history completion

Make `_step_completed_in_history` treat a `run-phase-review` history entry carrying a
`needs_work`/`incomplete_phase` verdict as **non-terminal** — it does not count as a
completed entry for completion inference. Any other terminal entry (a `pass`
verdict, a `recovered` entry, or a non-review step) still terminates the node as
today. Reuse the existing `record._phase_review_verdict(entry)` reader (already reads
`evidence.outputs.phase_review_report.verdict` from a history entry) via the
lazy-import precedent already in `readiness.py` (it imports `REPEAT_PREDICATES` from
`record.py`).

- **Pros:** Surgical (one predicate, ~5 lines). Keeps history-authoritative
  crash-resume intact (a crashed review has no needs_work verdict, so it still
  terminates). Keeps `status: completed` in history → zero metrics ripple. Keys on
  the domain's own source of truth (the verdict), so multi-round histories resolve
  correctly: round-N `needs_work` entries stay non-terminal, the final `pass` entry
  terminates the node.
- **Cons:** `readiness.py` gains a (lazy) dependency on a `record.py` reader — but
  the import direction and precedent already exist.
- **Complexity:** S

### Approach 2: Status downgrade (OQ-1 candidate a)

When opening the rework loop, write the needs_work `step_history` entry with a
non-terminal status (e.g. a new `superseded` marker) instead of `completed`, so
`_step_completed_in_history` (unchanged) no longer matches it.

- **Pros:** Localised to `record.py`; `readiness.py` untouched.
- **Cons:** Introduces a new status value into the status set, which ripples into
  the metrics DDL and every consumer that enumerates statuses
  (`extract_review_scores`, `compute_retries`, report views). A needs_work review
  would also vanish from the terminal-entry metrics unless each consumer is updated.
  Higher blast radius for the same outcome.
- **Complexity:** M

### Approach 3: Per-attempt review nodes (OQ-1 candidate c)

Model each re-review as a fresh node (`run-phase-review-2`, `-3`, …) so history
entries are per-attempt and never collide on node id.

- **Pros:** Conceptually clean; each attempt has its own node and history entry.
- **Cons:** Requires node-injection plumbing on every needs_work round, dependency
  rewiring for each new node, and changes to `compute-prediction-accuracy`'s
  chain-dep resolution to target the latest attempt node. Largest change; most new
  state surface; most regression risk against ORC-63/ORC-65.
- **Complexity:** L

### Selected Approach

**Approach 1 (Verdict-aware history completion).** Auto-selection heuristic:
complexity map S=2, M=3, L=4 → lowest numeric complexity is Approach 1 (S=2); no tie,
so reuse/alphabetical tiebreakers do not apply. It is also the only approach that
satisfies both binding constraints simultaneously — it preserves ORC-85's
history-authoritative crash-resume (ruling out a plain revert of the ORC-85 guard)
*and* keeps `status: completed` in history so metrics do not ripple (ruling out
Approach 2's new-status cost). A naive "don't infer completion when node status is
`in_progress`" was rejected outright: `test_dispatch_retry_storm.py:315`
(`recovered` entry + `in_progress` node must terminate) proves crash-recovered nodes
are legitimately `in_progress`, so node-status is not a safe discriminator — the
verdict is.

## High-Level Design

### Architecture Overview

Two cooperating layers, both already present; the change is confined to the
readiness inference and a regression test, plus an end-to-end test of the
already-present escalate branch.

```
record.py (record)                         readiness.py (DAG-walk)
─────────────────────                      ────────────────────────
needs_work verdict
  → mark node in_progress  (1688)          next_ready_node / is_node_ready
  → append history entry                     → _effective_node_status(node)
    {status: completed,                          → if node.status == completed: done
     evidence.outputs                            → _step_completed_in_history(node):
       .phase_review_report                          ← CHANGE: a run-phase-review
       .verdict: needs_work}                            entry with a needs_work/
                                                        incomplete_phase verdict is
escalate (cap hit)                                      NOT terminal
  → mark node completed                        → else: node.status (in_progress)
  → entry.status = blocked                   ⇒ run-phase-review re-emitted
  → state.status = paused                      after fix nodes complete (its
  (already implemented; activated             depends_on now points at last fix node)
   once re-emission loop closes)
```

### Key Abstractions

- **`_step_completed_in_history(state, node_id)`** — the single completion-inference
  predicate. Gains verdict-awareness: a terminal entry whose
  `record._phase_review_verdict(entry.raw)` is in `{needs_work, incomplete_phase}` is
  skipped (treated as non-terminal). All other terminal entries behave as today.
- **`record._phase_review_verdict(entry)`** — existing reader, reused (not
  duplicated) to extract the verdict from a history entry's
  `evidence.outputs.phase_review_report`.
- **`_rework_loop_active` / `_max_retry_rounds`** — existing cap machinery,
  unchanged; activated as a side-effect of the re-emission loop closing.

## Low-Level Design

### Components

- **`readiness._step_completed_in_history`** (edit): before returning `True` for a
  terminal entry, if the entry is a `run-phase-review` rework verdict, continue
  (skip it). Lazy-import `_phase_review_verdict` from `record` (same pattern as the
  existing `REPEAT_PREDICATES` lazy import) to avoid a module-load cycle.
- **`record.py` rework branch** (no change expected): `mark_node_status(in_progress)`
  on retry and the escalate branch already exist. The increment of
  `retries["run-phase-review"]` is emitted by the reviewer agent via
  `state_patch.retries` and merged by `_apply_state_patch` (`record.py:1642`)
  *before* `_rework_loop_active` reads it (`record.py:1682`) — ordering verified. See
  Decisions for the counter-ownership resolution.

### Data Flow

1. Reviewer returns `needs_work`; `record` merges `state_patch.retries` (counter↑),
   appends history `{status: completed, …verdict: needs_work}`, marks node
   `in_progress` (retry) or `blocked`+`paused` (escalate at cap).
2. `expand-plan` (already run by reviewer) has rewired `run-phase-review.depends_on`
   to the last `task-fix-N` node.
3. DAG-walk schedules the fix node(s). After they complete,
   `_effective_node_status(run-phase-review)` now returns `in_progress` (the
   needs_work history entry is no longer counted terminal), so `is_node_ready`
   re-emits `run-phase-review`.
4. Re-review runs (attempt N+1). On `pass`, the history entry is terminal →
   `compute-prediction-accuracy` becomes ready. On `needs_work` again, repeat until
   pass or the cap escalates (exit 2, paused).

### State Management

State lives in `state.yaml`: `workflow_plan[implement].nodes[*].status` (node
status), `step_history[*]` (terminal records with verdicts), and `retries`
(per-step counter). `record` writes node-status and history in one atomic state.yaml
write, so there is no persisted "completed-in-history / in_progress-in-plan" window
to recover from for promoted plans — the overlay's only legitimate job is
crash-mid-write and legacy plans, both preserved.

### Error Handling

- Missing/malformed `evidence` or verdict in a history entry → `_phase_review_verdict`
  returns `None` → the entry is treated as terminal (today's behaviour). Fail-safe:
  an unreadable verdict never *blocks* completion, it only declines the re-run
  exception.
- Legacy `active:[ids]` plans have no `node.status` to re-arm, so the verdict-aware
  skip is inert for them (matches prior behaviour; covered by existing legacy test).
- Cap exhaustion is the deliberate block: `entry.status = blocked`, `state = paused`,
  next `orchestrator next` exits 2.

## Constraints

- `readiness.py` must not introduce a module-load import cycle with `record.py` — use
  the established lazy-import-inside-function pattern.
- `step_history` entry status must remain `completed` for needs_work reviews (metrics
  invariant).

## Trade-offs

`readiness.py` takes a lazy runtime dependency on a `record.py` helper, coupling the
two modules a little more tightly. Accepted because the dependency direction
(readiness → record) and the lazy-import precedent already exist, and the
alternative (duplicating the verdict-reader in readiness) would create two readers of
the same nested shape that could drift.

## Acceptance Criteria

- AC-1: Given a `run-phase-review` node re-armed to `in_progress` after a `needs_work`
  verdict and a completed `task-fix-N`, When the DAG-walk computes readiness, Then
  `readiness.next_ready_node` returns `run-phase-review` (not the next
  declaration-order node), and the review's `retries["run-phase-review"]` counter is
  greater than its pre-rework value. [traces: UC-1]
- AC-2: Given a re-review returns `verdict: pass` with
  `overall >= quality_bar.min_phase_review_score`, When the DAG-walk computes
  readiness, Then `run-phase-review` is terminal and `compute-prediction-accuracy`
  becomes the next ready node. [traces: UC-2]
- AC-3: Given `retries["run-phase-review"]` has reached `quality_bar.max_retry_rounds`
  and the verdict is still `needs_work`, When `record` processes the verdict, Then the
  entry status is `blocked`, `state.status` is `paused`, and the next
  `orchestrator next` exits 2 (no advance to the next node). [traces: UC-E1]
- AC-4: Given a `needs_work` history entry (`status: completed`, verdict `needs_work`)
  for a node whose plan status is `in_progress`, When `_effective_node_status` is
  computed, Then it returns `in_progress` (the needs_work entry is non-terminal);
  And given a crash-recovered entry (`status: recovered`, no needs_work verdict) on an
  `in_progress` node, Then `_effective_node_status` still returns `completed`
  (ORC-85 crash-resume preserved). [traces: UC-E2]

## Decisions

- Discriminate "completed once, must re-run" from "completed for good" on the
  **verdict**, not the node status → node status is not safe
  (`test_dispatch_retry_storm.py:315`: a `recovered`+`in_progress` node must
  terminate) → keeps crash-resume correct while re-arming rework reviews.
- Keep `step_history` status `completed` for needs_work reviews; do not add a status
  value → avoids the metrics-DDL / consumer ripple Approach 2 incurs → metrics and
  `extract_review_scores` unchanged.
- Reuse `record._phase_review_verdict` rather than duplicate the reader → single
  source of truth for the nested history-verdict shape → minor readiness→record
  coupling, mitigated by the existing lazy-import precedent.
- **Retry-counter ownership: the engine owns the increment.** The retry counter is
  gating logic — it drives the cap → exit 2 (AC-3). Leaving it agent-emitted recreates
  the exact "dispatcher trusts an agent control signal" failure class this ticket was
  filed to kill. Verified facts forcing this: (1) nothing in the engine increments
  `retries["run-phase-review"]` today; (2) `_apply_state_patch` does
  `existing.update(retries)` — an *absolute overwrite* of whatever count the reviewer
  emits, not an add (`record.py:389`, comment: "payload sends absolute retry counts,
  not deltas"). So the counter only moves on agent trust. Fix: `record` increments
  `retries["run-phase-review"]` in the rework retry branch (next to the
  `mark_node_status(in_progress)` re-arm, `record.py:~1688`), deterministically. The
  reviewer prompt's `state_patch.retries` emission (run-phase-review/prompt.md step
  7d) is neutralized so engine + reviewer don't both increment (which would cap at 4
  rounds instead of 8). `_rework_loop_active` is a pure function of its `retries` arg,
  so its unit tests are unaffected; only the `record()`-level round behaviour changes.

## Open Questions

- (Resolved in Decisions) OQ-1 → Approach 1; OQ-2 → escalate branch needs no change,
  only the re-emission loop closing; OQ-3 → engine owns the `retries["run-phase-review"]`
  increment; reviewer's `state_patch.retries` emission neutralized.
