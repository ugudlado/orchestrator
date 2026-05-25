Run the workflow learning pipeline for this completed change.

1. Read state.yaml from $WORKFLOW_STATE_DIR/$CHANGE_ID/ to get the change ID
   and confirm the workflow reached the complete phase.

2. Run the full evaluation, finding classification, rule routing, hit/miss
   update, decay evaluation, and quality bar adjustment per the workflow-learner
   agent pipeline.

3. If learning fails for any reason: log learn_skipped: true and return success.
   Learning is best-effort and must not fail the complete phase.

4. Return COMPLETION per contracts/done-payload.md.
