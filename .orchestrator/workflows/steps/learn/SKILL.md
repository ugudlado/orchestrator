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

- Optional: `proposed-scenarios.jsonl` next to this run's `state.yaml` (created
  when durable learnings need new eval scenarios).
- Optional COMPLETION `outputs.backlog_tickets_synced` (list, when tickets were filed).
  No `learn_result` handle — step `status` plus any written files are enough.

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
   behavior: convert it into an eval scenario and **propose** it by appending
   one JSON line to `proposed-scenarios.jsonl` in the directory containing this
   run's state.yaml (create the file if absent). Do not edit any pack's
   `scenarios/*.jsonl` yourself — the `persist-learnings` step that runs right
   after this one validates every proposed row, appends the survivors to the
   target pack's `scenarios/train.jsonl`, and commits them. Writing directly
   risks a malformed row, which makes the whole pack unevaluable.
   Format, one physical line per proposal (no pretty-printing):
   `{"step_id": "<target step>", "row": {"id": "<short-kebab-slug>", "scenario": "<the situation>", "expect": ["...", "..."]}}`
   `step_id` names the step whose future behavior the learning changes; it must
   be a key of `$ORCHESTRATOR_PROMPT_DIRS` (a JSON object mapping `step_id` →
   absolute prompt dir for every agent step in this workflow). A learning about
   a step absent from that map has nowhere to land — skip it. `row` carries
   exactly the three keys `id`, `scenario`, `expect` and nothing else.
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
       reason: "learn cycle finished (or gated off — see logs)"
       backlog_tickets_synced: []
   ```
   If learning was gated off / not listed by the workflow, still return
   `status: completed` (learning is best-effort). Put the skip detail in
   `outputs.reason` and chat/logs (`learn_skipped: true`, `learn_error: ...`) —
   do not invent a `learn_result` output. COMPLETION status is only
   `completed` or `failed`.

### Rules (constraints on how)

- Learning failure is non-blocking — if /learn fails, log a warning and return success.
- Read state.yaml from the active change directory (this step runs before archive).
- On autopilot runs, rule changes apply without user confirmation.
- Never skip the learn step during autopilot — it feeds the self-improving loop and must run on every autopilot run. A `skipped: true` outcome is only valid when the step is gated off (e.g. learn=false) or simply not listed by the running workflow. Session token budget, time pressure, 'capture via retro', or any cost-based justification is NEVER a valid skip reason for feedback-loop steps. Budget pressure is a signal to stop earlier, not to skip learning.

## Verify

- Step completed (either learn_completed or learn_skipped recorded)
- If learn_completed: /learn produced output (check for cycle metrics or rule updates)
- If learn_skipped: learn_error contains a meaningful reason
