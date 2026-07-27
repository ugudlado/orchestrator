# Explore Step Metrics

Metric keys: `problem_space_focus`, `discovery_contract`

Use these metrics only for explore-specific scenarios. Base explorer metrics are
scored separately on base scenarios.

| Metric                | 10 looks like                                                                                                                | 0 looks like                                                                              |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `problem_space_focus` | Surveys constraints, conventions, and integration points; treats the ticket as scope source of truth; defers all design.     | Proposes solution designs, invents scope beyond the ticket, or skips the codebase survey. |
| `discovery_contract`  | discovery.md has every required section, ≥2 use cases, explicit open questions and build-or-reuse decision; rerun guard ran. | Missing sections, hidden open questions, no use cases, or redoing an archived change.     |
