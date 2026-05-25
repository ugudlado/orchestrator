Run the workflow learning pipeline for this completed change.

1. Read the active state.yaml for this change. Prefer `state_yaml_path` from the
   dispatch prompt (worktree runs: under `worktree_path/spec/changes/<change_id>/`;
   non-worktree: `$REPO_ROOT/spec/changes/<change_id>/`). Do not read from archive
   or from `$REPO_ROOT/spec/changes/` while a worktree path is set — merge and
   archive happen only in the later `complete-workflow` step.

2. Run the full evaluation, finding classification, rule routing, hit/miss
   update, decay evaluation, and quality bar adjustment per the workflow-learner
   agent pipeline.

3. If learning fails for any reason: log learn_skipped: true and return success.
   Learning is best-effort and must not fail the complete phase.

4. Return COMPLETION per contracts/done-payload.md.
