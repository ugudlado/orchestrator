# Run UX Critique Step Metrics

Metric keys: `critique_gating`, `fix_loop_discipline`

Use these metrics only for step-specific scenarios. Base ux-reviewer metrics are
scored separately on base scenarios.

| Metric                | 10 looks like                                                                                                    | 0 looks like                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `critique_gating`     | UI-file detection correct; clean skip with log when no UI; critique performed directly against target users/bar. | Critiquing non-UI phases, skipping real UI changes, or delegating to /critique.    |
| `fix_loop_discipline` | Fixes scoped to findings, verify_commands re-run, retries counted in state.yaml, escalation at max_retries.      | Unscoped fixes, broken verify ignored, infinite retries, or silent low-score pass. |
