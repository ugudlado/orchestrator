# Implement Tasks

**Intent:** Work through all pending tasks in `tasks.yaml` in dependency order. For each
task: implement the change, run verification, commit, then update `status: completed` in
`tasks.yaml`. Skip tasks already marked `status: completed`.

## Inputs

- `design.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md` — design, acceptance
  criteria, and component breakdown.
- `tasks.yaml` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml` — ordered task list
  with `status` field per task.
- **Patch schema** runs may have neither file — see the Pre-flight stub below.

## Outputs

- `implementation_result` — summary of tasks completed this pass.
- Updated `tasks.yaml` with `status: completed` on every finished task.

## Instructions

### Pre-flight

1. Read `design.md` for context: goals, acceptance criteria, component breakdown.
2. **Both `design.md` and `tasks.yaml` absent (patch schema)? Do NOT block or abandon** —
   Read `$ORCHESTRATOR_CONFIG/steps/implement-tasks/reference/edge-cases.md` before
   proceeding; it tells you to derive work from `ticket-context.md` instead.
3. Read `tasks.yaml`. Identify all tasks where `status` is `pending` (or absent).
   Tasks with `status: completed` are done — skip them entirely.
4. Resolve execution order: respect `depends_on` — do not start a task until all
   its dependencies have `status: completed`.
5. **Shell capability probe**: before starting the first task, run `git status` and `echo ok` to confirm shell commands are not blocked. If either command fails or is rejected, record the failure in `known_concerns` and abandon immediately — do NOT attempt any task. This prevents wasting tool budget on a task loop that cannot commit. <!-- learned: 2026-06-02, source: orc-118, cycle: 76, hits: 4, misses: 2, repo: orchestrator -->

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
  outputs:
    implementation_result: completed
    tasks_completed: <N>
    tasks_skipped: <N>
    known_concerns: [<list or empty>]
```

Hit a non-mainline outcome — zero tasks attempted, or partial progress then an
unrecoverable blocker? Read `$ORCHESTRATOR_CONFIG/steps/implement-tasks/reference/edge-cases.md`
for the abandoned / partial completion forms.

Facing a design contradiction, missing design coverage, or scope ambiguity? See
`$ORCHESTRATOR_CONFIG/steps/implement-tasks/reference/edge-cases.md` for the
escalation-to-architect protocol.

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
- If git commit commands cannot be executed (shell rejected, permission error, or any failure that prevents the commit from landing in HEAD), do NOT return `implementation_result: completed` — record the failure in `known_concerns` AND stop implementation. A task is only complete when its commit is confirmed in `git log`. Returning completed with uncommitted work causes the phase reviewer to flag a critical finding (CF) that blocks the phase. <!-- learned: 2026-06-02, source: orc-87, cycle: 76, hits: 4, misses: 2, repo: orchestrator -->

## Verify

- All `verify` commands for every completed task pass
- `tasks.yaml` has `status: completed` on every task implemented this pass
- One commit per task exists in git log (unless task produced no file changes)
