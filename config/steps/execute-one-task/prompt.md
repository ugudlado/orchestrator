# Execute One Task

**Intent:** Implement a single task from the expand-plan DAG. The agent reads the task payload from step_context.task, implements it, runs verification, commits, and returns COMPLETION for that one task. No scheduling logic, no loop.

## Inputs

The task payload arrives via `step_context.task` (id, title, files, verify, test_scenarios, change) — not a named input handle. The old contract declared `inputs: []` precisely because the input is delivered through `step_context`, not a named handle.

## Outputs

- `task_execution_result` — COMPLETION output handle for this one task.

## Instructions

### Single-Task Implementation

You have been spawned to implement exactly one task. The task payload is in
`step_context.task`:

```
step_context.task:
  id:             <task id, e.g. T-3>
  title:          <one-line description>
  files:          [list of files you are allowed to touch]
  verify:         [commands to run before COMPLETION]
  test_scenarios: [human-readable cases your tests must cover]
  why:            <which design AC this serves>          # optional
  change:         <the mechanism — what edit, at which line>  # optional
```

### Steps

1. Read `step_context.task` to understand the scope.
2. Read context files (design.md, relevant source files) as needed.
3. Implement the change described in `step_context.task.change` (or inferred
   from `title` and `test_scenarios` if `change` is absent).
4. Cover all `test_scenarios` with tests.
5. Run every command in `step_context.task.verify`. Fix until all pass.
6. Commit after all verify commands pass:
   - Message: `<prefix>(<change-id>): <task-id> <task-title>` where prefix is `feat` for feature, `fix` for bugfix, `chore` for config/docs-only.
   - Stage only files changed by this task — do NOT `git add -A`.
   - Skip the commit if `git status --porcelain` shows no changes.
   - Include `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
7. Return COMPLETION with `task_execution_result: completed`.

### Scope constraint

Touch only the files in `step_context.task.files`. If you discover a
necessary file is missing from the list, add it to your COMPLETION's
`known_concerns` — do NOT modify unlisted files.

### Rules (constraints on how)

- Read step_context.task for the task to implement (id, title, files, verify, test_scenarios, change).
- Implement only the files listed in step_context.task.files — do not touch other files.
- Run every command in step_context.task.verify before returning COMPLETION.
- Commit after all verify commands pass — stage only task files, never `git add -A`.
- Return one COMPLETION block — no loop, no next-task scanning.

## Escalation

Escalate to architect (`STATUS: escalate_to_architect`) only for:
- **Design contradiction** — task instruction conflicts with design.md
- **Missing design coverage** — task requires a decision design.md doesn't address
- **Scope ambiguity** — unclear whether behavior is in/out of scope and wrong choice cascades
- **Architectural dependency** — structural decision that affects other tasks

Do NOT escalate for implementation details, test strategy, library usage, or retry failures.

When escalating, return:
```
STATUS: escalate_to_architect
type: <contradiction|missing_coverage|scope_ambiguity|architectural_dependency>
task_id: T-<N>
context: |
  <what the task requires, what design.md says, why they conflict>
question: |
  <single concrete question the architect must answer>
attempted: |
  <what you already tried or considered>
```

Escalation does NOT count as a retry — the architect resolves the question, then you continue the same task at the same attempt.

## Verify

- All commands in step_context.task.verify pass
- Commit exists for this task (staged only task files)
