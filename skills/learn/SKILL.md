---
name: learn
description: "Reflect on a completed run and propose workflow/prompt improvements. Use when learning from a run, writing retros, or improving workflows."
user-invocable: true
---

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
   behavior: convert it directly into an eval scenario and append it as one
   JSON line to `$ORCHESTRATOR_CONFIG/steps/<step_id>/pack/scenarios/train.jsonl`
   (create the file if absent; never touch dev/holdout — they are held out
   for validation). Format:
   `{"id": "<short-kebab-slug>", "scenario": "<the situation>", "expect": ["...", "..."]}`
   The scenario recreates the situation the learning guards against, phrased
   as a fresh task with no hint of the rule; `expect` lists 3-4 observable
   staff-level behaviors the rule demands. Skip it if an existing scenario in
   the step's scenarios/ already covers the same failure mode. Whether a
   learning stays is decided by eval evidence: the prompt-optimizer per-
   scenario report shows whether it still catches failures or has been
   internalized. Do NOT write to spec/project.yaml `learnings:` — that key is
   not read by the dispatcher.

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
- Never skip the learn step during autopilot — it feeds the self-improving loop and must run on every autopilot run. A `skipped: true` outcome is only valid when the step is gated off (e.g. learn=false) or simply not listed by the running workflow. Session token budget, time pressure, 'capture via retro', or any cost-based justification is NEVER a valid skip reason for feedback-loop steps. Budget pressure is a signal to stop earlier, not to skip learning.

## Verify

- Step completed (either learn_completed or learn_skipped recorded)
- If learn_completed: /learn produced output (check for cycle metrics or rule updates)
- If learn_skipped: learn_error contains a meaningful reason
