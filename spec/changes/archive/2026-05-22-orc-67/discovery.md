---
feature-id: orc-67
linear-ticket: N/A
---

# Discovery Brief: Implement run-phase-review needs_work rework loop

## Feature Summary

When the reviewer agent returns a `needs_work` verdict, the current engine ignores it and advances linearly to the next step. The rework loop is fully specified in `run-phase-review.yaml` (step 7c) and `error-recovery.md` (row 17) — generate fix tasks, then re-run the review — but `record.py`'s `_compute_next_step` is verdict-blind and always computes the next step from DAG readiness alone. This feature closes the gap: a `needs_work` verdict must send the engine back to `execute-next-task` (to drain the newly-appended fix tasks), then re-dispatch `run-phase-review`, bounded by `max_retries` with `on_max_retries` escalation when exhausted.

## Personas & Actors

- **Orchestrator dispatch loop** (`skills/orchestrate/SKILL.md`): the driver that calls `orchestrator next` / `orchestrator done` in a loop. Currently has no verdict-awareness.
- **Reviewer agent** (`agents/reviewer.md`): emits `COMPLETION` with `verdict: needs_work` or `verdict: pass`, appends fix tasks to `tasks.md`, and increments `state_patch.retries`.
- **`record.py` CLI** (`config/scripts/orchestrator_next/record.py`): receives the done payload, applies `state_patch`, marks the node `completed`, then calls `_compute_next_step`. This is the last authoritative writer of `next_step`.
- **`readiness.py` DAG walker** (`config/scripts/orchestrator_next/readiness.py`): the single source of node readiness. `mark_node_status` is the only mutator. `is_node_ready` returns False for any `completed` node — there is no current concept of re-queuing a completed node.
- **`dispatch.py`** (`config/scripts/orchestrator_next/dispatch.py`): reads `next_step` from state.yaml and resolves the action; has no verdict-awareness.

## Use Cases

### Happy Path

UC-1: needs_work with retries remaining — reviewer agent completes a phase review, verdict is `needs_work`, retries < max_retries; engine dispatches `execute-next-task` to drain the fix tasks the reviewer appended, then redispatches `run-phase-review`, so quality issues are resolved before the phase is declared complete.

UC-2: needs_work exhausts retries — reviewer agent returns `needs_work`, `retries.run-phase-review >= max_retries`; engine executes the `on_max_retries` escalation action (escalate or ticket per `error-recovery.md § Escalation Protocol`) and does not silently advance.

UC-3: pass verdict — reviewer returns `verdict: pass`; engine advances linearly to the next step after `run-phase-review` in declaration order; no behavior change from today.

### Error & Edge Cases

UC-E1: reviewer emits needs_work but no fix tasks — reviewer increments retries and returns `needs_work` but does not append any fix tasks to `tasks.md`. The rework loop must still dispatch `execute-next-task`; `all_tasks_completed` will be true immediately and the engine will bounce back to `run-phase-review` for another try. This is not a correctness error but may cause tight loops — the design step should decide whether to detect and break this.

UC-E2: reviewer emits incomplete_phase — this is a distinct verdict meaning some tasks are unchecked. The current contract treats `incomplete_phase` differently from `needs_work` (no `review_score`). The rework loop behavior for this verdict is unspecified in the ticket; the design step must decide whether it gets the same re-dispatch treatment.

UC-E3: retries key absent or malformed in state.yaml — `state_patch.retries` from prior reviews is the source; `_apply_state_patch` already handles this by defaulting to an empty dict. The rework loop must not crash when `retries.run-phase-review` is absent (treat as 0).

UC-E4: max_retries sourced ambiguously — the reviewer reads `quality_bar.max_retry_rounds` from project.yaml (currently 8 for this repo, per `spec/project.yaml:193`). The grammar.yaml `verify_block.max_retries` is a different slot. The rework loop must read from the same source the reviewer uses; misreading the wrong key would produce inconsistent retry counting.

## Scope

### In Scope

- Verdict-aware next-step computation for `run-phase-review` on `needs_work`: dispatch `execute-next-task` instead of advancing.
- Retry ceiling check: when `retries.run-phase-review >= max_retries`, trigger `on_max_retries` escalation instead of re-dispatching.
- `pass` verdict leaves happy-path behavior unchanged (no regression to existing linear advance).
- Composing correctly with ORC-63's `nodes`-shape `workflow_plan` and `readiness.py`'s `next_ready_node`.
- Tests: at least one test per AC (needs_work + retries < max → execute-next-task; needs_work + retries >= max → escalation action; pass → linear advance).

### Out of Scope

- `run-ux-critique` has the same retry-loop shape (`run-ux-critique.yaml` step 43) — generalising the pattern to cover it is a follow-up; this feature targets only `run-phase-review`.
- Adding a new `repeat_until` predicate to `run-phase-review` itself (the contract does not declare one today; whether to use that mechanism is a design decision).
- Changes to the `orchestrator next` / `orchestrator done` CLI interface — ORC-63 non-goal carried forward.
- Changes to `state.yaml` schema shape — all writes stay via the existing `record.py` / `readiness.mark_node_status` path.
- Rework loop for general step failures (already handled by `error-recovery.md` Step Failure row via dispatch attempt increment).

## UI Direction

N/A — no UI components; this is an engine/dispatcher change.

## Technical Context

### Key files and locations

| File | Role | Relevant entry points |
|------|------|-----------------------|
| `config/scripts/orchestrator_next/record.py` | CLI record command; sole `next_step` writer | `_compute_next_step` (line 1181), `_apply_state_patch` (line 104), `_validate_phase_review_output` (line 74), `record()` main fn (line 1223), ordering: `_apply_state_patch` at 1470 → `mark_node_status` at 1494-1498 → `_compute_next_step` at 1501 |
| `config/scripts/orchestrator_next/readiness.py` | DAG walker; single node-status mutator | `next_ready_node`, `is_node_ready`, `mark_node_status` |
| `config/scripts/orchestrator_next/dispatch.py` | Dispatch: state.yaml → action JSON | `_compute_attempt` (step_history scan, line 39) |
| `config/steps/run-phase-review.yaml` | Reviewer step contract | Step 7b (needs_work COMPLETION shape), step 7c (rework instruction) |
| `config/steps/contracts/error-recovery.md` | Retry/escalation protocol | Row 17 (phase verify failure), § Escalation Protocol (on_max_retries table), § Fix Task Protocol |
| `skills/orchestrate/SKILL.md` | Dispatch loop driver | No verdict-check today; verdict-aware branch would be added here for locus (b) |
| `config/grammar.yaml` | Schema grammar | `verify_block.max_retries` (line 116), `verify_block.on_max_retries` (line 117) |
| `spec/project.yaml` | Repo quality config | `quality_bar.max_retry_rounds: 8` (line 193) |
| `config/workflows/feature.yaml` | Workflow step list | `execute-next-task` at position 6, `run-phase-review` at position 8 |

### Prior art in the codebase

**`repeat_until` special case (closest analog to the needed rework loop):**
`_compute_next_step` (record.py:1198-1213) already implements a "stay on step" mechanism: when the just-completed step declares `repeat_until: all_tasks_completed` and the predicate is False, the function returns the same step instead of advancing. The `_repeat_until_pending` branch (record.py:1494-1498) also keeps the node `in_progress` so the DAG-walk skips it. A verdict-aware locus in `record.py` would mirror this exact pattern.

**`retries` accounting:**
- `state_patch.retries` is a dict `{step_id: count}` emitted by the reviewer and merged by `_apply_state_patch` using absolute counts (not deltas): `existing.update(retries)`.
- `_compute_attempt` in dispatch.py derives attempt from step_history scan (independent of `retries`).
- `extract_review_scores` (record.py:897-925) already filters out `needs_work` and `incomplete_phase` when computing `review_score_avg`, confirming the verdict enum is load-bearing in existing metrics.

**`_STATE_PATCH_KEYS` whitelist:**
`frozenset({retries, quarantine_events, baseline, refresh_artifacts, change_type, flag_adaptations})` — `next_step` is not in this set. The reviewer cannot redirect next_step via `state_patch`. Any redirect must happen either inside `_compute_next_step` (locus a), in the driver (locus b), or via a DAG back-edge (locus c).

### DAG shape constraint

`is_node_ready` returns False for any node with `status == "completed"` (readiness.py:75-76). For a `needs_work` re-dispatch, `run-phase-review` has just been marked `completed`. To make it dispatchable again via the DAG, its status would need to revert to `pending` or `in_progress` — a mutation `mark_node_status` already supports, but which has no precedent in the current engine (nodes only move forward today).

Similarly, if `execute-next-task` is `completed` and `run-phase-review`'s verdict requires draining new fix tasks, `execute-next-task` must also become re-dispatchable. The existing `repeat_until` machinery already does this (keeping it `in_progress` rather than `completed` when the predicate is False), so newly-appended fix tasks are naturally drained if `execute-next-task` was left `in_progress` at review time. This is a key interaction the design step must reason about.

### Contract disagreement (open)

`error-recovery.md` row 17 says: phase verify failure → `step_history[].status: failed`, increment `retries.phase_verify`. `run-phase-review.yaml` step 7b says: return COMPLETION with `verdict: needs_work` (status `completed`). The current reviewer emits `status: completed` with the `needs_work` verdict. The two contracts disagree on the terminal status of a failed review round. The design step must reconcile before implementing.

### Existing tests that constrain the solution

| Test file | What it covers |
|-----------|---------------|
| `tests/test_repeat_until.py` | `_compute_next_step` re-emit semantics; must stay green |
| `tests/test_record_validation.py` (class `TestCheckD`) | `verdict` enum validation at record boundary; tests `needs_work` and `pass` are accepted, invalid verdicts are rejected |
| `tests/test_readiness.py` | `is_node_ready`, `mark_node_status`, `next_ready_node`; completed-node semantics must not regress |
| `tests/test_dispatch_resume.py` | `_compute_attempt` step_history scan; re-dispatch attempt increment must remain correct |
| `tests/test_boundary_detection.py` | `_detect_boundary` — phase/feature boundary logic |
| `tests/test_feature_metrics_compute.py` (classes `test_excludes_needs_work_verdict`, `test_excludes_incomplete_phase_verdict`) | `extract_review_scores` filters non-pass verdicts; must stay green regardless of loop locus |

## Approaches Considered

### Approach A: Verdict-aware branch in `record.py` `_compute_next_step` (mirror `repeat_until`)

**Core idea:** After `_apply_state_patch` (which sets `retries.run-phase-review`), `_compute_next_step` reads the just-completed step's verdict from the payload. If the step was `run-phase-review` and verdict is `needs_work`, check `retries` vs `max_retries`. Below ceiling: return `{phase, step_id: "execute-next-task"}` and reset `run-phase-review` node status to `pending`. At ceiling: emit escalation action.

**Build vs reuse:** Extend `record.py` `_compute_next_step` and add a small verdict-reader. Mirrors the `repeat_until` special case already in the same function.

**Pros:**
- Consistent with the existing special-case pattern (`repeat_until`).
- `next_step` is computed in one place; driver needs no new verdict awareness.
- `_STATE_PATCH_KEYS` whitelist is not at issue — `next_step` is always set by `record.py`.
- Retry count is available immediately after `_apply_state_patch` — no state read latency.

**Cons:**
- `_compute_next_step` becomes more complex: a pure DAG-walk is now mixed with verdic-specific imperative logic.
- Setting node status backward (`pending` from `completed`) has no precedent; it bypasses the DAG's forward-only invariant.
- `max_retries` source (project.yaml vs grammar.yaml `verify_block`) must be resolved and read at record time.
- The escalation action on ceiling is not a simple `next_step` — it requires setting `state.status = "paused"` or creating a Linear ticket. That side-effect does not fit cleanly in a function that returns a `next_step` dict.

**Effort:** Medium — touches `record.py` (already complex), needs new test coverage for all three verdict branches.

---

### Approach B: Driver-side verdict check in `skills/orchestrate/SKILL.md` dispatch loop

**Core idea:** After `orchestrator done` records a `run-phase-review` step, the driver reads `verdict` from the agent's COMPLETION output before calling `orchestrator next`. If `needs_work` and below ceiling, the driver manually queues `execute-next-task` by temporarily overriding `state.yaml.next_step` (or by calling `orchestrator next` knowing the normal advance will land there if `execute-next-task` is still `in_progress`). At ceiling, driver executes escalation.

**Build vs reuse:** Adds a verdict-check block to the SKILL.md dispatch loop prose. No Python changes.

**Pros:**
- Keeps `_compute_next_step` a pure DAG-walk with no verdict awareness.
- Escalation (set paused, create ticket) is natural for the driver to execute — it already handles exit code 2 (blocked) escalations.
- Reviewer's COMPLETION output is available directly to the driver without re-reading state.yaml.

**Cons:**
- Splits the "what step comes next?" logic between `record.py` (Python, tested) and SKILL.md (prose, not unit-testable).
- Driver must read the verdict correctly from the COMPLETION block format — brittle if format drifts.
- Requires the driver to either rewind `state.yaml.next_step` or depend on `execute-next-task` still being `in_progress`. The latter is fragile (depends on `repeat_until` predicate being false at the point of `run-phase-review` dispatch, which is the expected case but not guaranteed).
- Adds language-model-executed branching that today's clear protocol (parse COMPLETION, pipe to `orchestrator done`, loop) avoids.

**Effort:** Small in Python, medium in prose complexity and test surface.

---

### Approach C: DAG back-edge in `workflow_plan` nodes shape

**Core idea:** Declare an explicit `depends_on` back-edge in the node definition: `run-phase-review` has a dependency on itself with a retry predicate, or `execute-next-task` declares a conditional edge from `run-phase-review` with verdict=needs_work. `readiness.py` learns to handle a conditional back-edge, making a `completed` node re-eligible when the condition holds.

**Build vs reuse:** Extends `readiness.py` (DAG walker), grammar.yaml (new edge shape), `generate_plan.py` (plan promotion), and the `workflow_plan` node schema. Aligns with the DAG epic (ORC-63/64/65) that already contemplates richer graphs.

**Pros:**
- Most composable long-term: rework loops become declarative in the workflow schema rather than imperative Python.
- `run-ux-critique`'s identical retry shape would generalize automatically.
- Consistent with ORC-65 direction (per-task DAG nodes).

**Cons:**
- Largest surface area: touches grammar, readiness, generate_plan, test_readiness — well beyond the bug fix.
- Back-edges in a DAG make it a general graph; cycle detection and termination reasoning become harder.
- `generate_plan.py` must be updated to recognize the new edge shape; `workflow_plan` in state.yaml changes shape for nodes with back-edges.
- `readiness.is_node_ready` currently short-circuits on `status == "completed"` — the predicate-conditional check would need to run first, adding complexity to the hottest path in the engine.
- Out-of-scope for this ticket by the ticket's own framing ("related to the DAG epic").

**Effort:** Large — architectural change touching grammar, DAG walker, plan promotion, and tests.

## Recommendation

Defer locus choice to the design step. All three approaches are viable; the choice turns on three design-level commitments that this survey cannot make:

1. Whether `_compute_next_step` should remain a pure DAG-walk or absorb verdict-specific logic (Approach A vs the others).
2. Whether escalation on ceiling (`status: paused`, Linear ticket) belongs in `record.py`, in the driver, or in a new engine action — this is a behavioral scope question, not a research question.
3. Whether the DAG back-edge is wanted now as ORC-65 prior work, or strictly deferred.

Approach A (verdict branch in `record.py`) is the most contained for the narrow bug fix; Approach B (driver-side) keeps Python clean but moves tested logic to prose; Approach C (DAG back-edge) is aligned with the long-term epic but is disproportionately large for this ticket. The design step must commit to one and reconcile the `error-recovery.md` / `run-phase-review.yaml` contract disagreement on terminal status before implementation.

## Key Decisions

- **Locus: Approach A — verdict-aware branch in `record.py`** (OQ-3). Auto-selection heuristic: A and B tie on complexity (M=3); A wins on module reuse — it extends the `repeat_until` pattern already inside `_compute_next_step` and the node-status flip block. C (DAG back-edge, L=5) is out of scope. The decisive engine fact: `dispatch.py` selects the next step purely from `readiness.next_ready_node` over node statuses — it never reads `state.next_step`. So the rework lever is `readiness.mark_node_status`, exactly as `repeat_until` works today.
- **Contract reconciliation** (OQ-1): a `needs_work` review is `status: completed` + `verdict: needs_work` (matches `run-phase-review.yaml` step 7b and the reviewer's actual emission); `error-recovery.md` row 17 is edited to agree.
- **`max_retries` source** (OQ-2): `quality_bar.max_retry_rounds` from `project.yaml` — the same key the reviewer reads.
- **Node status after `needs_work`** (OQ-4/OQ-5): `run-phase-review` is left `in_progress`; the `execute-next-task`..`run-phase-review` segment is reset to `in_progress` so the DAG walk drains fix tasks then re-reviews. No new "backward mutation" precedent.
- **Escalation on ceiling**: record.py downgrades the recorded entry to `status: blocked` (so `orchestrator next` exits 2 and the driver halts) and sets `state.status = "paused"`.
- **`incomplete_phase`** (OQ-6): same re-dispatch treatment as `needs_work`.
- **`run-ux-critique`** (OQ-7): explicit follow-up, out of scope.

Full rationale and consequences: `design.md` § Decisions.

## Open Questions

- OQ-1: **Terminal status of a needs_work review.** `error-recovery.md` row 17 specifies `status: failed`; `run-phase-review.yaml` step 7b specifies status `completed` with `verdict: needs_work`. The reviewer currently emits `completed`. Which is authoritative? Resolving this determines whether `_compute_next_step` ever sees a `needs_work` verdict on a `completed` record (current behavior) or whether a `failed` terminal status triggers a different code path.

- OQ-2: **max_retries source.** The reviewer reads `quality_bar.max_retry_rounds` from project.yaml (currently 8). The grammar defines `verify_block.max_retries` as a separate slot. Which key should the rework loop ceiling check against, and where is it read — in `record.py` (Python, reads project.yaml), in the reviewer agent prompt (agent-side), or from a standard field in state.yaml?

- OQ-3: **Locus choice: record.py vs driver vs DAG back-edge.** The three approaches have materially different tradeoffs. Which locus best fits the project's trajectory — in particular, does ORC-65 (per-task DAG nodes) make Approach C worth the extra scope now?

- OQ-4: **`run-phase-review` node status after needs_work.** If locus (a) or (b): when `run-phase-review` has just been recorded as `completed` and the rework loop must re-dispatch it, should the node's status be reset to `pending` (backward mutation) or left `completed` and the re-dispatch happen outside the DAG walk (special-casing in `_compute_next_step` analogous to `repeat_until`)?

- OQ-5: **`execute-next-task` state at rework-loop re-entry.** If `execute-next-task` was left `in_progress` when `run-phase-review` was dispatched (which happens when tasks.md still has unchecked items at review time — an inconsistency the `incomplete_phase` verdict guards against), newly appended fix tasks are naturally drained. If it was `completed`, its node must be reset. The design step should clarify which state `execute-next-task` is in at rework loop entry and how that interacts with `repeat_until`.

- OQ-6: **`incomplete_phase` verdict and the rework loop.** The ticket specifies behavior for `needs_work` only. Should `incomplete_phase` also trigger a re-dispatch of `execute-next-task`, or is it handled separately (driver reads `next_step` as already pointing at `execute-next-task` since the `repeat_until` predicate would be false)?

- OQ-7: **`run-ux-critique` scope.** `run-ux-critique.yaml` step 43 specifies an identical retry-loop shape. Should this ticket address it, or is it a follow-up after the pattern is established for `run-phase-review`?
