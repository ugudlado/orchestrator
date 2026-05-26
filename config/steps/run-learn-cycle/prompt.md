Run the workflow learning pipeline for this completed change.

1. Read the active state.yaml for this change. Prefer `state_yaml_path` from the
   dispatch prompt (worktree runs: under `worktree_path/spec/changes/<change_id>/`;
   non-worktree: `$REPO_ROOT/spec/changes/<change_id>/`). Do not read from archive
   or from `$REPO_ROOT/spec/changes/` while a worktree path is set — merge and
   archive happen only in the later `complete-workflow` step.

2. Run the full evaluation, finding classification, rule routing, hit/miss
   update, decay evaluation, and quality bar adjustment per the workflow-learner
   agent pipeline.

3. Sync retro.md to backlog. Read `<state_dir>/retro.md` if present and parse
   each `## ISSUE-N` block. For each issue whose `fix_direction` is non-empty
   and not already addressed, invoke the `backlog-manager` skill to triage —
   hand it the issue's title, category, severity, surfaced_at, detail,
   fix_direction, and dedup_key. The skill owns dedup-against-existing
   tickets (by dedup_key as a stable suffix or by title match), priority
   assignment, and selecting the active backend (Linear vs Backlog.md). Do
   not shell out to `backlog`/Linear CLIs directly — let the skill handle
   backend routing. Collect the ticket ids the skill returns into
   `backlog_tickets_synced` for the COMPLETION outputs. If retro.md is
   absent or empty, set `backlog_tickets_synced: []` and continue.

4. If learning or sync fails for any reason: log learn_skipped: true (or
   continue with partial sync) and return success. Both learning and sync
   are best-effort and must not fail the complete phase.

5. Return COMPLETION per contracts/done-payload.md.
