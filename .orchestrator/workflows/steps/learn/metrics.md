# Run Learn Cycle Step Metrics

Metric keys: `learning_pipeline`, `scenario_quality`

| Metric              | 10 looks like                                                                                                              | 0 looks like                                                                                      |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `learning_pipeline` | Reads active state.yaml (not archive), runs the full cycle, never skips for budget reasons, fails soft and logged.         | Skips learning under pressure, reads stale state, or lets a learning failure block completion.    |
| `scenario_quality`  | Learnings become fresh, rule-blind train scenarios with 3-4 observable expects; duplicates skipped; dev/holdout untouched. | Scenarios leak the rule, land in dev/holdout, duplicate existing coverage, or go to project.yaml. |

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
