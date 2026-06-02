---
feature-id: orc-119
linear-ticket: N/A
---

# Discovery Brief: DAG Walker On-Failure Routing Bug

## Feature Summary

When a workflow node fails and its `on_failure` edge names an earlier step to re-run, `record.py` correctly resets that target node's `.status` to `pending` in `workflow_plan`. However, `readiness._effective_node_status` overrides that field with `step_history` — if a node was previously recorded as `completed` in `step_history`, it is treated as completed regardless of the node's current `.status` field. This causes `is_node_ready` to skip the reset target entirely, allowing downstream nodes (e.g., `ticket-start`) that have their dependency satisfied by the now-completed failed node to run instead. Observed in ORC-117: `design-review` failed, `on_failure` should have re-queued `design-and-draft-artifacts`, but `ticket-start` ran instead because `design-and-draft-artifacts` had a completed `step_history` entry from its prior run.

## Personas & Actors

- **Orchestrator engine** — the DAG walker that selects the next ready node after each step completes.
- **Workflow author** — declares `on_failure` edges in `feature.yaml`/`bugfix.yaml` to route retries.
- **Agent operator** — runs workflows expecting that a failed quality gate re-triggers the upstream fix step, not the downstream post-fix step.

## Use Cases

### Happy Path

UC-1: Retry after design-review failure — the orchestrator records `design-review` as failed, resets `design-and-draft-artifacts` to pending via `on_failure`, and the next `orchestrator next` call dispatches `design-and-draft-artifacts` (not `ticket-start`).

UC-2: Retry after run-phase-review failure — the orchestrator records `run-phase-review` as failed, resets `implement-tasks` to pending via `on_failure`, and the next dispatch picks `implement-tasks` (not `ticket-qa`).

### Error & Edge Cases

UC-E1: Reset target has no prior step_history entry — the `on_failure` reset works correctly today (the existing test fixture covers this), because `_effective_node_status` sees no history override and reads the node field directly.

UC-E2: Retry cap exhausted — `_resolve_routing` returns `halt_cap_exceeded`; the fix must not interfere with the halt path (no reset occurs when cap is reached).

UC-E3: on_failure target is a node that was itself never the reset target before — first failure scenario; the prior `step_history` completed entry must be cleared or ignored so the node is treated as ready.

## Scope

### In Scope

- Fix `readiness._effective_node_status` (or `is_node_ready`) to correctly treat a node whose `.status` field is `pending` as ready, even when `step_history` has a prior `completed` entry — specifically when the node has been explicitly reset via `on_failure` routing.
- Alternatively: have `record.py` remove the stale `step_history` completion entry for the reset target when performing an `on_failure` reset, so the override no longer fires.
- Add a regression test that reproduces the actual observed failure: a state where the `on_failure` target has a `completed` entry in `step_history`, and assert that `next_ready_node` returns the target (not a downstream node) after the failure is recorded.

### Out of Scope

- Changing the `on_failure` routing logic in `_resolve_routing` — the routing decision is correct; only the readiness check is wrong.
- Modifying the retry-cap/halt logic — it is not affected by this bug.
- Changing how `step_history` is populated for successful runs — the override is needed for replays and resume; the fix must be surgical.

## UI Direction

N/A — no UI components. This is a pure engine fix in `orchestrator_next/readiness.py` and/or `orchestrator_next/record.py`.

## Key Decisions

- **Fix location — readiness vs record**: Two approaches:
  1. `readiness.py`: teach `_effective_node_status` to NOT treat step_history completion as authoritative when the node's `.status` field is explicitly `pending` (i.e., `.status` wins if it is `pending`).
  2. `record.py`: when performing an `on_failure` reset, strip the target node's prior `completed` entries from `step_history` (or add a tombstone mechanism).

  Approach 1 is simpler and more surgical — the node `.status` field is the canonical write-path for `on_failure` resets. Approach 2 mutates step_history which is intended as an append-only audit log. **Recommendation: Approach 1** — give the explicit node `.status = pending` field priority over step_history in `_effective_node_status`. Concretely: `if node.status == "pending": return "pending"` before the step_history override check.

- **Test fixture gap**: The existing `_nodes_state` fixture in `test_rework_loop.py` does NOT include a `step_history` completed entry for `execute-next-task`, so the bug is not covered. A new test must include a prior completed step_history entry for the reset target to reproduce the real-world failure.

- **Selected direction (design-and-draft-artifacts)**: **Approach 1** — in
  `_effective_node_status`, short-circuit to `"pending"` when the node's `.status` field
  is the literal string `"pending"`, before the `step_history` override fires. Chosen on
  the complexity heuristic (XS vs Approach 2's S) and because it preserves the
  append-only `step_history` contract. Complexity: **XS**. See design.md Selected Approach.

## Open Questions

- OQ-1: **RESOLVED.** The `_step_completed_in_history` override serves replay/resume
  (a node terminal in history must not re-dispatch after a partial state write). The fix
  keys on the literal `"pending"` string only — not absent/`None` — so the override path
  is unchanged for every status except an explicit `pending` write. The resume invariant
  is preserved (AC-3 covers it).
- OQ-2: **RESOLVED.** `grep -rn 'mark_node_status([^)]*"pending"')` over `orchestrator_next/`
  and `config/` returns exactly one hit: `record.py:1287`, the on_failure reset. No other
  code path writes `.status = "pending"` to a node, so no legitimately-completed node is
  ever simultaneously `pending` + history-completed except an on_failure reset target.
  The fix cannot mis-promote a node that should stay completed.
