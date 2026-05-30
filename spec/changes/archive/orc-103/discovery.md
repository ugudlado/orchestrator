---
feature-id: orc-103
linear-ticket: ORC-103
---

# Discovery Brief: needs_work rework loop does not re-run phase review after fix task

## Feature Summary

When `run-phase-review` returns a `needs_work` (or `incomplete_phase`) verdict, the orchestrator's ORC-67 rework loop is supposed to inject fix task-nodes, run them, and then **re-run `run-phase-review` (attempt N+1)** — only advancing past the gate once the verdict passes (score ≥ `quality_bar.min_phase_review_score`), capping rounds at `quality_bar.max_retry_rounds`. In the orc-96 run (2026-05-29) the engine spawned `task-fix-1` correctly and the fix committed, but after the fix completed the workflow advanced straight to `compute-prediction-accuracy` instead of re-entering `run-phase-review`. The quality gate is therefore not actually closed by the rework loop: a wrong or incomplete fix can ship because nothing re-verifies after it. This change closes that gate so a `needs_work` verdict reliably forces re-review until the score passes or the retry cap is exhausted (which must block, not silently advance).

## Personas & Actors

- **Orchestrator engine** (`record.py` rework loop + `readiness.py` DAG walk) — the system actor that decides the next node after a step completes.
- **Reviewer agent** (`run-phase-review` step) — emits the `needs_work`/`pass` verdict, appends fix tasks to `tasks.yaml`, and invokes `expand-plan`.
- **Developer agent** (`task-fix-N` node) — implements the fix and commits it.
- **Feature workflow owner / user** — relies on the gate to guarantee a passing review before a feature advances toward merge; receives the exit-2 block when retries are exhausted.

## Use Cases

### Happy Path

UC-1: Re-review after a fix — the engine, after a `needs_work`-spawned fix task completes, wants to re-dispatch `run-phase-review` (incrementing its attempt) so that the fix is independently re-verified rather than assumed correct.
UC-2: Advance on pass — the engine wants to advance past the review gate to `compute-prediction-accuracy` only after `run-phase-review` returns `verdict: pass` with `overall >= quality_bar.min_phase_review_score`, so that no unresolved `needs_work` verdict is carried forward.

### Error & Edge Cases

UC-E1: Retry cap exhausted — what happens when re-review keeps returning `needs_work` up to `quality_bar.max_retry_rounds`: the engine must escalate/block (exit 2, state paused) rather than silently advancing to the next declaration-order node.
UC-E2: Re-emission vs. crash-resume inference — what happens when a node is deliberately reset to `in_progress` for re-run, yet a terminal `completed` entry for that step already exists in `step_history`: the DAG walk must treat the node as still-to-run, not infer it complete from history.

## Scope

### In Scope

- Make a `needs_work`/`incomplete_phase` verdict reliably re-dispatch `run-phase-review` after the spawned fix task-node(s) complete.
- Gate advance past `run-phase-review` on a passing verdict (`overall >= quality_bar.min_phase_review_score`).
- Cap re-review rounds at `quality_bar.max_retry_rounds`; on exhaustion, block (exit 2) and pause the workflow rather than advancing.
- Regression test: a `needs_work` verdict followed by a completed fix task re-enters `run-phase-review` (attempt incremented), not the next step; and the exhaustion path blocks.

### Out of Scope

- Redesigning the ORC-63 declaration-order DAG walk or making the dispatcher honor an agent-emitted `next_step` — that fights the "DAG-walk-is-single-source-of-truth" architecture and is the wrong layer for the fix (the emitted `next_step` framing in the ticket is the symptom, not the mechanism).
- Changing the reviewer's fix-task generation / `expand-plan` insertion logic — fix-node injection already works (verified in orc-96: `task-fix-1` spawned and committed).
- Changing crash-resume / reconcile semantics beyond what is needed to distinguish "completed for good" from "completed once, must re-run."
- The `pass` linear-advance path and non-rework verdicts — unchanged.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Direction: verdict-aware history completion (design Approach 1, complexity S).**
  Make `readiness._step_completed_in_history` treat a `run-phase-review` history
  entry carrying a `needs_work`/`incomplete_phase` verdict as non-terminal (reusing
  `record._phase_review_verdict`), so it no longer overrides the deliberate
  `in_progress` re-arm. Chosen over OQ-1 (a) status-downgrade (M — ripples into the
  status set + metrics DDL) and OQ-1 (c) per-attempt nodes (L — most new state
  surface). Auto-heuristic: lowest complexity (S) with no tie.
- **Root cause confirmed:** ORC-85 (`f796938`) dropped the `_uses_legacy_active_plan`
  guard, broadening history-authoritative completion to promoted plans; that is
  correct for crash-resume but invalidated ORC-67's `in_progress` re-arm. The
  discriminator must be the verdict, not the node status — `test_dispatch_retry_storm.py:315`
  proves a crash-`recovered` node is legitimately `in_progress` and must still
  terminate.
- **OQ-2 resolved:** the `max_retry_rounds` escalate branch is correct as-is; it is
  merely dead until the re-emission loop closes. No change to the escalate branch;
  AC#3 re-verified end-to-end.
- **OQ-3 resolved: engine owns the counter increment.** Verified that nothing in the
  engine increments `retries["run-phase-review"]` today and that `_apply_state_patch`
  *overwrites* it with the reviewer's absolute count (`record.py:389`). Since the
  counter gates the cap (AC#3), leaving it agent-emitted repeats this ticket's own
  root failure (trusting an agent control signal). Fix: `record` increments the
  counter in the rework retry branch; the reviewer prompt's `state_patch.retries`
  emission is neutralized to avoid double-counting.

## Open Questions

- OQ-1: Root cause — `record.py` (retry branch, ~lines 1687–1688) sets the `run-phase-review` *node* to `in_progress` to re-arm it, but leaves the appended `step_history` entry at `status: completed` (the needs_work review genuinely completed). `readiness._effective_node_status` (readiness.py ~84–91) then infers the node is `completed` from that history entry via `_step_completed_in_history`, overriding the `in_progress` status. So the DAG walk skips re-emission and `compute-prediction-accuracy`'s implicit chain-dep on `run-phase-review` is satisfied, advancing the workflow. Which mechanism should be changed to disambiguate "completed for good" from "completed once, must re-run"? Candidate directions (for design to choose, not prescribed here): (a) downgrade the recorded `step_history` status for the needs_work entry (e.g. to a non-terminal/`recovered`-excluded marker) so history-inference no longer matches it; (b) exclude deliberately-reset (`in_progress`) nodes from history-completion inference; (c) model each re-review as a fresh attempt-node so history entries are per-attempt and never collide.
- OQ-2: The `max_retry_rounds` escalate machinery already exists in `_rework_loop_active` (escalate branch in record.py downgrades the entry to `blocked` and sets state `paused`), but it is currently dead code — because re-review never fires, `retries["run-phase-review"]` never increments past round 1 and escalate is never reached. Does the fix need any change to the escalate branch itself, or only to the re-emission path that feeds it (so the existing cap starts working once the loop closes)? AC#3 must be re-verified end-to-end after the re-emission fix lands.
- OQ-3: What is the exact attempt-increment contract for AC#1 ("attempt incremented")? Confirm whether `retries["run-phase-review"]` is incremented by the reviewer agent's `state_patch.retries` (per run-phase-review/prompt.md step 7d) or must be incremented by the engine on re-dispatch — and that the regression test asserts against the right counter.

<!-- Format contract: config/steps/explore/prompt.md § Discovery Brief Format Contract -->
