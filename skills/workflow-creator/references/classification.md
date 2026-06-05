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

| Step                                  | Route                                                | Why                                      |
| ------------------------------------- | ---------------------------------------------------- | ---------------------------------------- |
| Run tests + fix failures              | **Split**                                            | `run-tests` shell; `fix-failures` prompt |
| Lint + apply fixes                    | **Split**                                            | `run-lint` shell; `fix-lint` prompt      |
| Generate outline from brief           | prompt                                               | Structure requires judgment              |
| Convert markdown → PDF via pandoc     | shell                                                | Fixed command                            |
| SEO keyword research                  | prompt                                               | Synthesis and selection                  |
| Export course pack (zip fixed layout) | shell                                                | Deterministic packaging                  |
| Peer review                           | prompt                                               | Subjective rubric                        |
| Check word count ≥ 1000               | shell                                                | `wc -w` threshold                        |
| Assess readability grade              | shell if formula (flesch script); prompt if holistic |
| expand-plan (read tasks.yaml)         | shell                                                | Existing orchestrator step               |
| draft lesson content                  | prompt                                               | Creative                                 |

## Splitting compound steps

If research suggests one phase that mixes both (e.g. “Develop content and run QA”):

1. `draft-content` — prompt
2. `run-qa-checks` — shell (link checker, spellcheck CLI)
3. `review-content` — prompt (holistic review)

Smaller steps improve resume, cost attribution, and clarity.

## Reusing existing steps

Before creating a new step, check `config/steps/`. Reuse when behavior and I/O match
(e.g. `archive-completed-change`, `cost-report`, `expand-plan`). Otherwise create a new step
id — do not overload unrelated steps.

## Agent picker (probabilistic steps)

| Activity                | Typical `agent:`   |
| ----------------------- | ------------------ |
| Research, discovery     | `discoverer`       |
| Structure, spec, design | `architect`        |
| Creative exploration    | `ideator`          |
| Implementation          | `developer`        |
| Code/content review     | `reviewer`         |
| UX review               | `ux-reviewer`      |
| Process learnings       | `workflow-learner` |

Step-specific detail belongs in `prompt.md`, not in picking a exotic agent name.
