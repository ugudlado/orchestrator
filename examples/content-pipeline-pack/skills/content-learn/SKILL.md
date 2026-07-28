---
name: content-learn
description: "Reflect on a finished content run and convert durable lessons into eval scenarios beside the step that needs them. Use as the last step of a content workflow."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#operator
---

# Content Learn

**Intent:** Turn what went wrong in this run into a scenario that will catch it next time, stored beside the step whose behavior must change.

## Inputs

- The run's artifacts (`brief.md`, `outline.md`, `draft.md`, `final.md`) in the artifact directory.
- `$ORCHESTRATOR_PROMPT_DIRS` — JSON object mapping `step_id` to the absolute prompt directory for every prompt step in this workflow.

## Outputs

- Zero or more appended lines in other steps' `scenarios/train.jsonl`.
- `learn_result` — `completed` or `skipped`.

## Instructions

1. Read the run's artifacts from `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/$ORCHESTRATOR_CHANGE_ID/`. Note where the pipeline did rework: a rejected edit, a section that had to be redrafted, a constraint that was missed and caught late.

2. For each durable lesson — one that would change a specific step's future behavior, not a one-off about this topic — identify **which step** should have behaved differently. A missed brief constraint belongs to the step that should have caught it, not to whichever step surfaced it.

3. Append the lesson as one JSON line to that step's training bank. Do not resolve the directory yourself: `$ORCHESTRATOR_PROMPT_DIRS` is a JSON object mapping `step_id` to its absolute prompt dir. Look the target step up in it and append to `<dir>/scenarios/train.jsonl`, creating the file if absent.

   Format:
   `{"id": "<short-kebab-slug>", "scenario": "<the situation>", "expect": ["...", "..."]}`

   The scenario recreates the situation the lesson guards against, phrased as a fresh task with no hint of the rule. `expect` lists three or four observable behaviors the lesson demands.

4. **Train split only.** Never write `dev.jsonl` or `holdout.jsonl` — they are held out for validation, and writing to them destroys the only unbiased signal the bank has. A step id absent from `$ORCHESTRATOR_PROMPT_DIRS` has no prompt directory to write beside; skip that lesson.

5. Skip a lesson whose failure mode an existing scenario in that step's `scenarios/` already covers. Duplicate scenarios inflate the bank without adding signal.

6. If reflection fails for any reason, record `learn_result: skipped` and return success. Learning is best-effort and must never fail the run.

7. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       learn_result: completed
   ```

### Rules (constraints on how)

- Colocation beside the charter is the whole rule — there is no separate blessed location for scenarios.
- One lesson, one line, one target step. Do not write a lesson to several steps hoping one sticks.
- A lesson must be durable. "This particular brief was vague" is not durable; "briefs that omit length need a stated assumption" is.
- Learning failure is non-blocking. Never return a failed status from this step.

## Verify

- Every appended line is valid JSON on a single line with `id`, `scenario`, and `expect` keys.
- Every write landed in a `scenarios/train.jsonl` under a directory named in `$ORCHESTRATOR_PROMPT_DIRS`.
- `dev.jsonl` and `holdout.jsonl` are untouched.
- `learn_result` is recorded either way.
