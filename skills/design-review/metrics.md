# Design Review Step Metrics

Metric keys: `gate_discipline`, `finding_actionability`

Use these metrics only for step-specific scenarios. Base reviewer metrics are
scored separately on base scenarios.

| Metric                  | 10 looks like                                                                                                         | 0 looks like                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `gate_discipline`       | Deterministic gate runs first; needs_work returns status failed with refresh_artifacts; caps applied (crit→4, imp→7). | Scoring past a failed gate, needs_work as completed, editing artifacts, wrong caps.     |
| `finding_actionability` | Each finding names the AC, task id, or section at fault with concrete guidance; no style nitpicks.                    | Vague findings, subjective preferences flagged, or findings without a repair direction. |

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
