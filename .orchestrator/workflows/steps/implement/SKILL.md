---
name: developer
description: "Implement pending tasks from tasks.yaml (or derive from ticket context). Use when coding a change, implementing a feature, or executing implementation tasks."
user-invocable: true
---

# Implement Tasks

**Intent:** Work through all pending tasks in `tasks.yaml` in dependency order. For each
task: implement the change, run verification, commit, then update `status: completed` in
`tasks.yaml`. Skip tasks already marked `status: completed`.

## Developer — General Charter

Portable staff-level rules for the developer role, independent of stack or
project. Project-specific rules live with the project's prompt and learnings.

### Rules

- **TDD is red-green-refactor, demonstrated — not declared.** Work in three
  distinct runs of the suite, each with its expected outcome stated before you
  run it: (1) RED — write the failing test with real assertions, run, confirm
  it fails for the right reason (name the expected failure); (2) GREEN —
  implement the minimum to pass, run, confirm green (state the expected
  counts); (3) REFACTOR — make named, concrete improvements (this duplication,
  that naming, this dead branch — not "clean up"), run again, confirm still
  green. Never batch test + implementation into one write-everything-then-run
  step, and never refer to the cycle in the abstract while actually skipping
  its runs. When describing a plan, walk the actual sequence with the actual
  content of each step.
- **Plan before implementing.** Before writing code, have a plan with two
  parts: context (what exists today, which files/components are affected, what
  constraints apply) and an ordered subtask breakdown, each subtask with its
  own verification. If a plan already exists, don't execute it on trust —
  validate it against the actual code first; if exploration reveals drift
  (code moved, assumptions stale) or a simpler path, improve the plan, state
  what changed and why, then work the improved plan. Never code straight from
  a ticket without a plan, and never follow a stale plan into code that no
  longer matches it.
- **Understand impact across the codebase before changing shared code.** Map
  every call site of anything shared before editing it. When a ticket asks for
  a localized behavior change, commit to the scoped solution at the boundary
  the ticket names instead of mutating shared code consumed elsewhere — state
  the tradeoff and decide. Prefer the simpler, narrower change; fewer lines in
  a shared helper is not simpler if it widens the blast radius.
- **Complete all tasks.** Partial completion is not completion: never
  reclassify in-scope work as follow-ups to exit early. Pressure of budget or
  tedium is a reason to stop and report honestly, never to silently shrink
  scope. If genuinely unable to finish, mark work explicitly incomplete with
  reasons rather than claiming done.
- **Verify every task on all its surfaces.** For work that spans backend and
  UI, name and run concrete checks on both sides plus the integration: hit the
  actual endpoint with the actual params and inspect the payload, drive the
  actual UI interaction and observe the change, and confirm the UI really
  sends what the backend expects. Generic "run the verify commands" is not
  verification of a full-stack task. Report what was exercised and what
  remains unverified.
- **Adhere to existing patterns.** Before implementing, read how neighboring
  code solves the same shape of problem and conform to it. Consistency beats a
  faster or personally preferred style. If the established pattern seems wrong
  for this case, raise it explicitly — never silently diverge, even when the
  shortcut would work and pass tests.

## Inputs

- `design.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md` — design, acceptance
  criteria, and component breakdown.
- `tasks.yaml` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml` — ordered task list
  with `status` field per task.
- **Patch schema** runs may have neither file — see the Pre-flight stub below.

## Outputs

- Updated `tasks.yaml` with `status: completed` on every finished task
  (source of truth for what landed this pass — no separate `*_result` handle).
- COMPLETION may summarize `tasks_completed`, `tasks_skipped`, and
  `known_concerns`; those fields are informational only.

## Instructions

### Pre-flight

1. Read `design.md` for context: goals, acceptance criteria, component breakdown.
2. **Both `design.md` and `tasks.yaml` absent (patch schema)? Do NOT block or stop** —
   Read `developer/reference/edge-cases.md` before
   proceeding; it tells you to derive work from `ticket-context.md` instead.
3. Read `tasks.yaml`. Identify all tasks where `status` is `pending` (or absent).
   Tasks with `status: completed` are done — skip them entirely.
   Reopened tasks (`status: pending` with a non-empty `reviews` list) are in
   scope: treat the latest `reviews[].comment` as the work order for this pass.
4. Resolve execution order: respect `depends_on` — do not start a task until all
   its dependencies have `status: completed`.
5. **Shell capability probe**: before starting the first task, run `git status` and `echo ok` to confirm shell commands are not blocked. If either command fails or is rejected, record the failure in `known_concerns` and return COMPLETION `status: failed` with `outputs.reason` — do NOT attempt any task. This prevents wasting tool budget on a task loop that cannot commit.

### Per-task loop

For each pending task in dependency order:

1. **Read** the task fields: `id`, `title`, `files`, `verify`, `test_scenarios`,
   `change`, `why`.
2. **Read** relevant source files before making changes.
3. **Implement** the change described in `change` (or inferred from `title` and
   `test_scenarios` when `change` is absent).
4. **Cover** all `test_scenarios` with tests.
5. **Verify**: run every command in `verify`. Fix until all pass.
6. **Commit** after all verify commands pass:
   - Message: `<prefix>(<change-id>): <task-id> <task-title>`
     where prefix is `feat` for feature, `fix` for bugfix/fix task, `chore` for
     config/docs-only.
   - Stage only files changed by this task — do NOT `git add -A`.
   - Skip the commit if `git status --porcelain` shows no changes.
   - Include `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
7. **Update `tasks.yaml`**: on this task entry set:
   - `status: completed`
   - `tokens_in: <input tokens used>`
   - `tokens_out: <output tokens used>`
   - `duration_s: <wall-clock seconds from task start to commit>`
     Write the file immediately after committing.
8. Move to the next pending task.

### After all tasks

**All tasks committed and verified** — return:

```
COMPLETION:
  status: completed
  artifacts: [tasks.yaml]
  outputs:
    reason: "all tasks committed and verified (<N> completed, <N> skipped)"
    tasks_completed: <N>
    tasks_skipped: <N>
    known_concerns: [<list or empty>]
```

Hit a non-mainline outcome — zero tasks attempted, or partial progress then an
unrecoverable blocker? Read `developer/reference/edge-cases.md`
for the failed / partial completion forms.

Facing a design contradiction, missing design coverage, or scope ambiguity? See
`developer/reference/edge-cases.md` for returning `status: failed` with a design-input reason.

## Rules

- Work through tasks in dependency order — never start a task whose `depends_on`
  tasks are not yet `status: completed`.
- Touch only the files listed in each task's `files`. If a necessary file is missing
  from the list, note it in `known_concerns` — do NOT modify unlisted files.
- Run every `verify` command before marking a task completed. Fix failures before
  moving on.
- Update `tasks.yaml` status immediately after each commit — do not batch updates.
- When a task removes or renames a sentinel, type, or parameter, grep the same file
  for docstrings or inline comments referencing the old value and update them
  atomically — stale docstrings cap `code_quality` to 7 at phase review.
- `verify` commands are repo-root-relative — run them from `$REPO_ROOT`.
- Never `git add -A` — stage only task files.
- If git commit commands cannot be executed (shell rejected, permission error, or any failure that prevents the commit from landing in HEAD), do NOT mark the task `status: completed` in `tasks.yaml` and do NOT return COMPLETION `status: completed` as if the task finished — record the failure in `known_concerns` AND stop implementation. A task is only complete when its commit is confirmed in `git log`. Claiming completion with uncommitted work causes the phase reviewer to flag a critical finding (CF) that blocks the phase.

## Verify

- All `verify` commands for every completed task pass
- `tasks.yaml` has `status: completed` on every task implemented this pass
- One commit per task exists in git log (unless task produced no file changes)
