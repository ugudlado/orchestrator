# Run UX Critique Step Metrics

Metric keys: `critique_gating`, `fix_loop_discipline`

Use these metrics only for step-specific scenarios. Base ux-reviewer metrics are
scored separately on base scenarios.

| Metric                | 10 looks like                                                                                                    | 0 looks like                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `critique_gating`     | UI-file detection correct; clean skip with log when no UI; critique performed directly against target users/bar. | Critiquing non-UI phases, skipping real UI changes, or delegating to /critique.    |
| `fix_loop_discipline` | Fixes scoped to findings, verify commands re-run, retries counted in state.yaml, escalation at max_retries.      | Unscoped fixes, broken verify ignored, infinite retries, or silent low-score pass. |

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
