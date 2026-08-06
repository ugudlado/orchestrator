---
name: human-review
description: "After code-review passes, gather human feedback. Approve to ship, or reset the DAG (implement/design/…) with updated tasks. Use when a feature is waiting on human input after review."
user-invocable: true
---

# Human Review

**Intent:** Pause for a human after automated `code-review` passes. Interpret
their freeform reply: approve and advance, ask again, or send the workflow back
to an earlier step with updated tasks / design notes.

## Context

- Upstream: `code-review` passed (see `code-review.md` and recent `tasks.yaml`).
- Resume text arrives as **User direction** in the prompt (CLI:
  `orchestrator feature <ticket> "<text>"`). Empty direction means first ask.
- Artifacts live under `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/` (and worktree paths
  from state). Prefer reading existing review artifacts over guessing.

## Allowed `reset_to` targets (feature workflow)

Only emit a step id that exists on this workflow:

`explore` · `ux-design` · `design` · `implement`

Do **not** invent ids. Prefer the earliest step that must re-run:

| Intent                                      | `reset_to`  |
| ------------------------------------------- | ----------- |
| Code / test fixes, new implementation tasks | `implement` |
| AC / design wrong, approach change          | `design`    |
| Discovery / problem framing wrong           | `explore`   |
| UI direction wrong                          | `ux-design` |

## Instructions

1. **Load context** — Skim latest `code-review.md` (or step_history summary) and
   `tasks.yaml`. Note what passed and what is still open.

2. **No User direction** — Do not invent approval. Record `await_input` with a
   short `ask` that:
   - States review passed (one line)
   - Invites freeform feedback **or** an explicit ship/approve intent
   - Mentions they can request design vs implement changes

3. **Unclear User direction** — Record `await_input` again with a clarifying
   `ask` (one question). Do not guess `reset_to`.

4. **Approve / ship** — Human clearly accepts the current work (e.g. ship,
   approve, LGTM, merge, “looks good”). Return `completed`. Do not reset.

5. **More work** — Human wants changes:
   - Update `tasks.yaml`: add new tasks and/or reopen existing ones with the
     feedback captured (notes / `reviews[]` per existing task schema).
   - If design/AC must change, update `design.md` (and tasks) consistently.
   - Return `failed` with `outputs.reset_to` set to the correct earlier step
     (table above). Include `outputs.reason` summarizing the human ask.
   - Engine resets that step and everything after it; you will be asked again
     after the next successful `code-review`.

## COMPLETION — need human input

```text
COMPLETION:
  step_id: human-review
  status: await_input
  outputs:
    ask: >
      Code review passed. Reply with approval to continue toward merge, or
      describe changes (I can send work back to implement or design).
```

## COMPLETION — approved

```text
COMPLETION:
  step_id: human-review
  status: completed
  outputs:
    reason: Human approved after code-review.
```

## COMPLETION — send back

```text
COMPLETION:
  step_id: human-review
  status: failed
  outputs:
    reset_to: implement
    reason: >
      Human asked for empty-title validation and a flaky-test fix; tasks.yaml updated.
    artifacts: [tasks.yaml]
```

## Rules

- Prefer `await_input` over guessing approval.
- Always set `reset_to` when status is `failed` for a rework path (otherwise
  the workflow falls back to static `on_failure: implement`).
- Never `reset_to` a step after `human-review` in the DAG.
- On re-entry after `await_input`, treat User direction as the answer to the
  previous `ask`.
