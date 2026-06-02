---
feature-id: orc-119
linear-ticket: N/A
---

# Design: DAG walker picks forward node over on_failure reset target

## Context

When a workflow node with an `on_failure` edge records `status: failed`, `record.py`
marks the failed node `completed` and resets the routing target (an earlier step) to
`pending` via `mark_node_status(state_raw, phase, routing, "pending")`
(`orchestrator_next/record.py:1287`). The intent is that the next `orchestrator next`
re-dispatches that reset target.

It does not. The DAG walker's readiness check,
`readiness._effective_node_status` (`orchestrator_next/readiness.py:76-83`), consults
`step_history` BEFORE honoring the node's `.status` field:

```python
def _effective_node_status(state, node):
    status = node.get("status")
    if status == "completed":
        return "completed"
    if _step_completed_in_history(state, _node_id(node)):   # <-- overrides pending
        return "completed"
    return str(status or "pending")
```

If the reset target ran successfully on a prior attempt, `step_history` holds a
`completed`/`recovered` entry for it. `_step_completed_in_history` returns True, so
`_effective_node_status` reports `completed` even though `.status` is now `pending`.
`is_node_ready` therefore skips the reset target, and `next_ready_node` returns the
next forward node in declaration order whose dependency is satisfied by the
(now-completed) failed node — e.g. `ticket-start` instead of `design-and-draft-artifacts`.

The `step_history` override exists for replay/resume: a node already terminal in
history must not be re-dispatched after a partial state write. The override is
correct for resume but wrong for the on_failure reset path, where `.status = pending`
is a deliberate, fresh write that must win.

## Goals / Non-Goals

### Goals

- An `on_failure` reset target whose `.status` is explicitly `pending` is treated as
  ready and re-dispatched, even when `step_history` has a prior `completed` entry for it.
- Forward nodes past the failure point do not run until the reset target completes.
- The replay/resume invariant the `step_history` override protects is preserved.

### Non-Goals

- No change to `_resolve_routing` — the routing decision (which target an `on_failure`
  edge picks) is already correct.
- No change to retry-cap / `halt_cap_exceeded` / `halt` logic.
- No change to how `step_history` is populated for successful runs (it stays an
  append-only audit log; Approach 2 is rejected — see below).

## Approaches Considered

### Approach 1: explicit `pending` wins in `_effective_node_status`

In `_effective_node_status`, return `"pending"` immediately when the node's `.status`
field is explicitly the string `"pending"`, before the `step_history` override fires.

- Pros: ~2-line change at the single readiness chokepoint; surgical; the node `.status`
  field is the canonical write-path for on_failure resets, so making it authoritative
  when explicitly `pending` is semantically correct. Preserves the resume override for
  every other status (absent/`None`/`in_progress`).
- Cons: relies on `record.py` being the only writer of `.status = "pending"` to a node
  (verified — see Decisions); a future code path that sets `.status = "pending"` on a
  node that should stay completed-by-history would also become ready. Acceptable: that
  is the correct behavior for an explicit pending write.
- Complexity: **XS**

### Approach 2: strip the stale `step_history` entry in `record.py`

When performing an on_failure reset, also remove (or tombstone) the target node's prior
`completed` entries from `step_history`, so the override no longer fires.

- Pros: leaves `_effective_node_status` untouched.
- Cons: mutates `step_history`, which is intended as an append-only audit log; loses the
  record that the target previously ran; a tombstone mechanism adds shape and a new
  invariant to every history reader (11+ consumers). Higher complexity, broader blast
  radius.
- Complexity: S

### Selected Approach

**Approach 1.** Lowest complexity (XS vs S) and it does not violate the append-only
`step_history` contract. The node `.status` field is already the canonical write target
for on_failure resets; making an explicit `pending` authoritative aligns the readiness
read with the reset write. Approach 2 was ruled out by the constraint that `step_history`
must remain append-only (discovery Out of Scope).

## High-Level Design

### Architecture Overview

`record.py` (write path) resets the routing target's node `.status` to `pending`.
`readiness.py` (read path) computes which nodes are ready for `dispatch.py` to spawn.
The bug is a read/write disagreement: the write sets `.status = pending`, the read
ignores it in favor of `step_history`. The fix aligns the read with the write at the
single readiness chokepoint, so both writers (`record.py` reset → `pending`,
successful completion → `completed`) and the next-node computation cannot drift.

### Key Abstractions

- `_effective_node_status(state, node) -> str` — the single function that reconciles a
  node's `.status` field with `step_history`. It is the only place the override lives,
  so the fix is one edit there.

## Low-Level Design

### Components

- `orchestrator_next/readiness.py :: _effective_node_status` — add a guard so an explicit
  `.status == "pending"` short-circuits to `"pending"` before the `step_history` override.

### Data Flow

`record.record()` → `mark_node_status(..., "pending")` writes `.status` on the reset
target → `_compute_next_step` → `next_ready_node` → `ready_nodes` → `is_node_ready` →
`_effective_node_status` reads `.status` and (today) `step_history`. After the fix,
the explicit `pending` returns early and the reset target is reported not-completed →
ready → dispatched.

### State Management

State lives in `state.yaml`: `workflow_plan[phase].nodes[].status` (the field the fix
honors) and `step_history[]` (unchanged, append-only). No new state introduced.

### Error Handling

No new failure modes. The guard reads an existing field; an absent/`None` status falls
through to the unchanged override path exactly as today.

## Constraints

- The fix must preserve the replay/resume invariant: a node terminal in `step_history`
  with no explicit `pending` write must still be treated as completed.
- `verify` commands must be repo-root-relative (project rule).

## Trade-offs

Sacrificed: the absolute invariant "step_history completion always wins." Accepted
because the explicit `.status = "pending"` write is, by construction, a deliberate
re-open signal — the only writer of `pending` to a node is the on_failure reset
(verified at HEAD). Treating it as authoritative is the intended semantics, not a
regression risk.

## Acceptance Criteria

- AC-1: Given a state where an `on_failure` reset target has `.status: pending` AND a
  prior `completed` entry in `step_history`, When `next_ready_node(state)` is computed,
  Then it returns the reset target (not a downstream forward node). [traces: UC-1, UC-E3]
- AC-2: Given a `record()` run that fails `run-phase-review` (on_failure →
  `execute-next-task`) where `execute-next-task` has a prior `completed` `step_history`
  entry, When the failure is recorded, Then `next_ready_node` / `next_step` resolves to
  `execute-next-task`, not `run-ux-critique` or any later node. [traces: UC-1, UC-2]
- AC-3: Given a node with no explicit `pending` write but a `completed` `step_history`
  entry (the resume case), When readiness is computed, Then it is still treated as
  completed and not re-dispatched. [traces: UC-E1]
- AC-4: Given the retry cap is exhausted (`halt_cap_exceeded`, no reset occurs), When
  the failure is recorded, Then the fix does not re-open any node and the halt/blocked
  path is unaffected. [traces: UC-E2]
- AC-5: The full existing `tests/test_rework_loop.py` suite continues to pass
  (no regression in the existing on_failure / escalation behavior). [traces: UC-E1, UC-E2]

## Decisions

- Approach 1 over Approach 2 → keeps `step_history` append-only and is XS vs S →
  one guarded early-return in `_effective_node_status`.
- OQ-2 closed: `grep -rn 'mark_node_status([^)]*"pending"')` over `orchestrator_next/`
  and `config/` returns exactly one hit — `record.py:1287`, the on_failure reset. No
  other code path writes `.status = "pending"` to a node, so giving an explicit
  `pending` priority cannot mis-promote a legitimately-completed node. The guard keys on
  the literal string `"pending"` only (not absent/`None`), so the resume override is
  untouched for every other case.

## Open Questions

- (none — OQ-1 resolved by keying on the literal `"pending"` string so the resume
  override path is preserved; OQ-2 closed in Decisions above by grep against HEAD.)
