---
name: human-review
description: "After code-review passes, gather human feedback. Approve to ship, or reset the DAG (implement/design/…) with updated tasks. Use when a feature is waiting on human input after review."
user-invocable: true
---

# Human Review

**Intent:** Pause for a human after automated `code-review` passes. Offer
deterministic options (approve / send back to a specific step); the engine
matches a label or option number and routes without re-dispatching this
step. Only genuinely freeform text (no option match) reaches you to
interpret — see the escape-hatch instruction below.

## Context

- Upstream: `code-review` passed (see `code-review.md` and recent `tasks.yaml`).
- Resume text arrives as **User direction** in the prompt (CLI:
  `orchestrator feature <ticket> "<text>"`). Empty direction means first ask.
- Artifacts live under `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/` (and worktree paths
  from state). Prefer reading existing review artifacts over guessing.

## Allowed `reset_to` targets (feature workflow)

Only emit a step id that exists on this workflow:

`explore` · `ux-design` · `design` · `implement`

## Instructions

1. **Load context** — Skim latest `code-review.md` (or step_history summary) and
   `tasks.yaml`. Note what passed and what is still open.

2. **First ask (no User direction yet)** — Return `await_input` with the
   options block below. Always include `approve` and at least the `implement`
   rework option; add `design`/`explore`/`ux-design` options only when there's
   a specific known concern for that layer.

3. **Escape hatch — User direction present but didn't match an offered
   option** (the engine already tried; you're only reached because nothing
   matched). Interpret it:
   - Clearly approval-shaped (ship / approve / LGTM / merge / "looks good") →
     return `completed`.
   - Describes concrete rework → update `tasks.yaml` (add/reopen tasks with
     the feedback in `reviews[]`) and, if design/AC changed, update
     `design.md` too. Return `failed` with `outputs.reset_to` set to the
     earliest step that must re-run and `outputs.reason` summarizing the ask.
   - Still unclear → return `await_input` again with a clarifying `ask` and
     the same (or narrowed) options list. Do not guess `reset_to`.

## COMPLETION — need human input

```text
COMPLETION:
  step_id: human-review
  status: await_input
  outputs:
    ask: "Code review passed. Ship it, or send back for changes?"
    options:
      - label: approve
      - label: rework implementation
        reset_to: implement
      - label: rework design
        reset_to: design
```

## COMPLETION — approved (escape hatch: freeform text read as approval)

```text
COMPLETION:
  step_id: human-review
  status: completed
  outputs:
    reason: Human approved after code-review.
```

## COMPLETION — send back (escape hatch: freeform text read as rework)

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
- The escape-hatch instructions only apply when the engine did NOT already
  match User direction to an offered option — that match short-circuits this
  step entirely (advance or reset applied directly, no re-dispatch).
