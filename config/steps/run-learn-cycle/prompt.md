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
   update, decay evaluation, and quality bar adjustment.

3. For each durable learning that should change a specific step's future
   behavior: append it to `$ORCHESTRATOR_CONFIG/steps/<step_id>/learnings.md`
   (create the file if absent) as a short plain markdown bullet — no metadata
   trailer or comment. Whether a learning stays is decided by eval evidence,
   not counters: each bullet becomes a prompt-optimizer train scenario
   (`run.py sync`), and its per-scenario scores in the pack's runs ledger show
   whether the rule is still catching failures or has been internalized. This
   file is separate from `contract.yaml`/`prompt.md` so a future
   `pack add --force` upgrade (which overwrites the pack's own prompt/contract)
   never clobbers it. Do NOT write to spec/project.yaml `learnings:` — that
   key is not read by the dispatcher.

4. If learning fails for any reason: log learn_skipped: true and return success.
   Learning is best-effort and must not fail the complete phase.

5. Return COMPLETION:
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
- Never skip the run-learn-cycle step during autopilot — it feeds the self-improving loop and must run on every autopilot run. A `skipped: true` outcome is only valid when the step is gated off (e.g. learn=false) or simply not listed by the running workflow. Session token budget, time pressure, 'capture via retro', or any cost-based justification is NEVER a valid skip reason for feedback-loop steps. Budget pressure is a signal to stop earlier, not to skip learning.

## Verify

- Step completed (either learn_completed or learn_skipped recorded)
- If learn_completed: /learn produced output (check for cycle metrics or rule updates)
- If learn_skipped: learn_error contains a meaningful reason
