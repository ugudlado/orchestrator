# Run Learn Cycle

**Intent:** Trigger automatic learning from the just-completed change so every completion improves the next execution.

## Inputs

- `final_signoff_decision` (optional) — names a human approval gate, not a dataflow edge.

## Outputs

- `learn_result`

## Instructions

Run the workflow learning pipeline for this completed change.

1. Read the active state.yaml for this change. Prefer `state_yaml_path` from the
   dispatch prompt (worktree runs: under `worktree_path/spec/changes/<change_id>/`;
   non-worktree: `$REPO_ROOT/spec/changes/<change_id>/`). Do not read from archive
   or from `$REPO_ROOT/spec/changes/` while a worktree path is set — merge and
   (mark-change-completed, compute-swe-metrics, cost-report, ticket-done) run before
   archive; merge and worktree teardown stay in `orchestrator complete`.

2. Run the full evaluation, finding classification, rule routing, hit/miss
   update, decay evaluation, and quality bar adjustment per the workflow-learner
   agent pipeline.

3. If learning fails for any reason: log learn_skipped: true and return success.
   Learning is best-effort and must not fail the complete phase.

4. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       learn_result: <completed|skipped>
   ```

### Rules (constraints on how)

- Learning failure is non-blocking — if /learn fails, log a warning and return success.
- Read state.yaml from the active change directory (this step runs before archive).
- On autopilot runs, rule changes apply without user confirmation.
- Never skip the run-learn-cycle step during autopilot — it feeds the self-improving loop and must run on every autopilot run. A `skipped: true` outcome is only valid when the step is gated off (e.g. learn=false) or simply not listed by the running workflow. Session token budget, time pressure, 'capture via retro', or any cost-based justification is NEVER a valid skip reason for feedback-loop steps. Budget pressure is a signal to stop earlier, not to skip learning. <!-- learned: 2026-04-05, source: HL-203, cycle: 6, hits: 26, misses: 1, repo: orchestrator -->

## Verify

- Step completed (either learn_completed or learn_skipped recorded)
- If learn_completed: /learn produced output (check for cycle metrics or rule updates)
- If learn_skipped: learn_error contains a meaningful reason
