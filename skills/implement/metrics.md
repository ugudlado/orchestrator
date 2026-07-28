# Orchestrator Developer Metrics

Metric keys: `workflow_protocol`, `task_state_integrity`

Use these metrics only for Orchestrator-specific scenarios. Base developer metrics are
scored separately on base scenarios.

| Metric                 | 10 looks like                                                                                                                     | 0 looks like                                                                           |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `workflow_protocol`    | Reads required artifacts, respects task dependencies and file boundaries, handles patch-schema and blocked-shell paths correctly. | Ignores task workflow, dependency order, or execution constraints.                     |
| `task_state_integrity` | Runs required verification, stages only task files, commits before completion state, and reports blockers as known concerns.      | Claims completion with failed verification, a failed commit, or inaccurate task state. |

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
