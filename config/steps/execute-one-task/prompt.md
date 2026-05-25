## Single-Task Implementation

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
6. Commit per `contracts/auto-commit.md`:
   `feat(<change-id>): <task-id> <task-title>`
7. Return COMPLETION with `task_execution_result: completed`.

### Scope constraint

Touch only the files in `step_context.task.files`. If you discover a
necessary file is missing from the list, add it to your COMPLETION's
`known_concerns` — do NOT modify unlisted files.
