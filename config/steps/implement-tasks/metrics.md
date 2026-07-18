# Orchestrator Developer Metrics

Metric keys: `workflow_protocol`, `task_state_integrity`

Use these metrics only for Orchestrator-specific scenarios. Base developer metrics are
scored separately on base scenarios.

| Metric                 | 10 looks like                                                                                                                     | 0 looks like                                                                           |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `workflow_protocol`    | Reads required artifacts, respects task dependencies and file boundaries, handles patch-schema and blocked-shell paths correctly. | Ignores task workflow, dependency order, or execution constraints.                     |
| `task_state_integrity` | Runs required verification, stages only task files, commits before completion state, and reports blockers as known concerns.      | Claims completion with failed verification, a failed commit, or inaccurate task state. |
