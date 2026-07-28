# Workflow Creator Metrics

Metric keys: `classification_correctness`, `reuse_before_create`, `confirmation_discipline`

Judge a response as an agent turning a user goal into orchestrator workflow config.
Score each metric from 0 to 10. The expected behaviors in the scenario remain the
concrete pass criteria; this rubric evaluates the quality and completeness of the
reasoning behind them.

| Metric                       | 10 looks like                                                                                                                                                                                                                                                                               | 0 looks like                                                                                                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `classification_correctness` | Classifies each step correctly as shell (deterministic) or prompt (probabilistic/LLM judgment), and never puts probabilistic work in shell or deterministic work in a prompt step. Treats reusable-vs-one-off as irrelevant to classification — both are `prompt:` pointing at a directory. | Misclassifies a step's nature, picks a step type based on convenience rather than what the step actually requires, or invokes a third step kind such as the removed `skill:` field. |
| `reuse_before_create`        | Scans existing workflows first and proposes extending or reusing a matching one when the user's goal overlaps; only proposes a new workflow when no existing one covers the goal.                                                                                                           | Creates a duplicate or near-duplicate workflow without checking what already exists.                                                                                                |
| `confirmation_discipline`    | Proposes the I/O contract and step table and gets explicit user confirmation before scaffolding any files.                                                                                                                                                                                  | Scaffolds files before the user has confirmed the plan, or skips presenting the plan entirely.                                                                                      |

## Scoring guidance

- Score the response's reasoning and proposed plan, not an imagined final workflow —
  most scenarios are offline and end before scaffolding actually runs.
- A response that correctly stops to ask a clarifying question (unclear schema name,
  ambiguous deliverable) should not be penalized on `confirmation_discipline`; that is
  the intended behavior for an underspecified ask.
- A critical violation — scaffolding files before user confirmation — caps
  `confirmation_discipline` at 2.

## Eval context

Scenarios are evaluated as a single text response with no file or shell
access. The response IS the artifact: score inline artifact content (file
bodies, sections, COMPLETION blocks) as the artifact itself, and explicitly
named commands with their expected outcomes as performed verification. Do
not penalize a response for being unable to literally write files or execute
commands in this context.
