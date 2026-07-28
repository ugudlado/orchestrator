# content-learn metrics

## targets_the_right_step

Each lesson is written to the step whose behavior must change, not to the step
that surfaced the problem. A brief constraint missed at outline and caught at
edit belongs to outline.

## train_split_only

Appends land only in `scenarios/train.jsonl`. Any write to `dev.jsonl` or
`holdout.jsonl` fails this metric outright — those splits are the bank's only
unbiased signal.

## resolves_via_prompt_dirs

The target directory comes from looking the step id up in
`$ORCHESTRATOR_PROMPT_DIRS`. Constructing a path by convention, guessing a
repo-relative location, or writing to a `pack/` directory fails. A step id
absent from the map is skipped rather than written somewhere plausible.

## lessons_are_durable

Scenarios generalize past this run's subject matter. A scenario that only makes
sense for this specific brief or topic fails; the rule it encodes must apply to
future unrelated runs.

## scenario_hides_the_rule

The scenario text recreates the situation without naming the rule being tested,
and `expect` lists three or four observable behaviors. A scenario that states
the lesson in its own prompt fails.

## non_blocking

Reflection failure records `learn_result: skipped` and returns success. A
failed status from this step fails this metric regardless of the cause.
