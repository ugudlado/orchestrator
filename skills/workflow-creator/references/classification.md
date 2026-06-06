# Step classification: deterministic vs probabilistic

Use this when a step is ambiguous during workflow design.

## Deterministic → shell (`kind: script`)

The step is fully specifiable as an algorithm:

- **File ops:** mv, cp, mkdir, archive directory, write fixed template with variable substitution
- **Tooling:** run `pytest`, `eslint`, `pandoc`, `ffmpeg` with fixed flags; pass/fail from exit code
- **Data:** jq/yq transform, CSV merge, checksum, count lines, validate schema against JSON Schema
- **Integrations:** POST webhook with templated body; backlog/Linear status change
- **Git:** add, commit with template message, tag, merge (when strategy is fixed)

**Test:** Could a human write a bash script today that completes the step without reading
for meaning? → shell.

## Probabilistic → prompt (`agent:` + `prompt.md`)

The step requires interpretation:

- **Research:** gather sources, summarize, recommend approach
- **Creation:** draft content, code, design, outline
- **Evaluation:** review quality, UX critique, compliance judgment
- **Planning:** break down work, estimate, prioritize with tradeoffs
- **Diagnosis:** root cause analysis, compare alternatives

**Test:** Would two competent people given the same input plausibly produce different
outputs? → prompt.

## Edge cases

| Step                                  | Route                                                | Why                                                                                                  |
| ------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `tests-pass`, `lint-and-fix`          | prompt (prefer inside pre-commit checks)             | Agent runs and fixes until green/clean; only add a dedicated step if pre-commit hooks can't cover it |
| Generate outline from brief           | prompt                                               | Structure requires judgment                                                                          |
| Convert markdown → PDF via pandoc     | shell                                                | Fixed command                                                                                        |
| SEO keyword research                  | prompt                                               | Synthesis and selection                                                                              |
| Export course pack (zip fixed layout) | shell                                                | Deterministic packaging                                                                              |
| Peer review                           | prompt                                               | Subjective rubric                                                                                    |
| Check word count ≥ 1000               | shell                                                | `wc -w` threshold                                                                                    |
| Assess readability grade              | shell if formula (flesch script); prompt if holistic |
| validate new workflow schema          | shell (`orchestrator validate-workflow <schema>`)    | CLI verb, not a workflow step                                                                        |
| draft lesson content                  | prompt                                               | Creative                                                                                             |

## Compound steps

If a proposed step mixes both (e.g. “Develop content and run QA”), design it as separate
atomic steps from the start — don't create a compound step and split later:

1. `draft-content` — prompt
2. `run-qa-checks` — shell (link checker, spellcheck CLI)
3. `review-content` — prompt (holistic review)

Smaller steps improve resume, cost attribution, and clarity.

## Reusing existing steps

Before creating a new step, check `config/steps/`. Reuse when behavior and I/O match
(e.g. `archive-completed-change`, `cost-report`). Otherwise create a new step
id — do not overload unrelated steps.

## Agent picker (probabilistic steps)

Don't pick from a fixed list. Check `skills/` for available agents and read each
`SKILL.md` to find the best fit for the step's activity. The agent roster changes
as new skills are added.

Match on what the step _does_, not on a keyword in its name. When no existing agent
fits well, `developer` is the safe default — it can execute most tasks given a clear
prompt.

Step-specific detail belongs in `prompt.md`, not in the agent name.
