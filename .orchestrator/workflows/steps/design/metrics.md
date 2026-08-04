# Design and Draft Artifacts Step Metrics

Metric keys: `artifact_contract`, `task_executability`

Use these metrics only for step-specific scenarios. Base architect metrics are
scored separately on base scenarios.

| Metric               | 10 looks like                                                                                                                        | 0 looks like                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `artifact_contract`  | design.md/tasks.yaml follow their format contracts; complexity heuristic applied and documented; claims verified against live code.  | Invented formats, undocumented approach selection, or unverified caller-site/shape claims.       |
| `task_executability` | Every task's verify is satisfiable within its file scope, repo-root-relative, RED tests carry xfail, phase gates match a green base. | Verify commands that block the developer: unlisted imports, absolute paths, unsatisfiable gates. |

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
