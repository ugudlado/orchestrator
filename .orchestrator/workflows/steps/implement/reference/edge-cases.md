# Implement Tasks — Edge Cases

Consult this file only when you hit one of the states named below. The mainline
"implement task → verify → commit → mark completed" loop lives in `prompt.md`.

## Patch workflow (no design.md / tasks.yaml)

When `design.md` and `tasks.yaml` are both absent, this is a patch-schema run.
Do NOT block or stop because design artifacts are missing.

- Read the ticket body from `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
  (`spec/changes/<slug>/ticket-context.md`, written by `load-ticket-context`).
  That file is the spec.
- Derive work items from its acceptance criteria and description.
- Create `tasks.yaml` in the artifact dir only when you need to track multiple
  items across commits. A single cohesive change may complete without ever
  writing `design.md` or `tasks.yaml`.

Pre-flight in this mode: use `ticket-context.md` in place of `design.md` for
context; derive the pending-task list from its acceptance criteria; then create
`tasks.yaml` if multiple commits are needed to track progress.

## Design needs architect input

Return COMPLETION `status: failed` only for:

- **Design contradiction** — task instruction conflicts with `design.md`
- **Missing design coverage** — task requires a decision `design.md` doesn't address
- **Scope ambiguity** — unclear whether behavior is in/out of scope and wrong
  choice cascades

Do NOT fail for implementation details, test strategy, or retry failures.

```
COMPLETION:
  status: failed
  outputs:
    reason: "design input needed (<contradiction|missing_coverage|scope_ambiguity>) — task <T-N>: <single concrete question>"
    known_concerns: ["<what the task requires, what design.md says, why they conflict>"]
```

## Non-mainline completion forms

The mainline outcome (all tasks committed and verified) is in `prompt.md`. Use
these two forms for the minority outcomes:

**Could not start — zero tasks attempted** (shell blocked, unresolvable blocker before T-1):

```
COMPLETION:
  status: failed
  outputs:
    reason: "<what prevented any work from starting>"
    tasks_completed: 0
```

**Partial progress — some tasks committed, then unrecoverable blocker**:

```
COMPLETION:
  status: completed
  artifacts: [tasks.yaml]
  outputs:
    reason: "partial progress — <blocker>; committed <N>, skipped <M>"
    tasks_completed: <N of committed tasks>
    tasks_skipped: <N remaining>
    known_concerns: ["<blocker description>"]
```

Only tasks whose commits landed in `git log` may have `status: completed` in
`tasks.yaml`. Do not emit an `implementation_result` handle.

Use `status: completed` whenever at least one task commit landed in `git log` —
even partial progress is a completed pass. Use `status: failed` when zero work
was done. COMPLETION status is only `completed` or `failed`.
