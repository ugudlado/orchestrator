# UX Design Step Metrics

Metric keys: `ux_gating`, `artifact_persistence`

Use these metrics only for step-specific scenarios. Base ux-designer metrics are
scored separately on base scenarios.

| Metric                 | 10 looks like                                                                                                        | 0 looks like                                                                       |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `ux_gating`            | Abandons cleanly when no UI surface; escalates on skill failure; bounded critique loop; sensible default on no pick. | Prototyping UI-less features, silent failure swallowing, unbounded critique loops. |
| `artifact_persistence` | ux-prototype.html and complete ux-artifacts.yaml persisted; UI Direction recorded back in discovery brief.           | Missing artifacts, incomplete artifact fields, or direction never written back.    |

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
