---
name: review
description: "Run the review workflow — quality gate + learn cycle against an existing implementation (ticket-review, run-phase-review, ticket-qa). Use after /implement when design/coding is already done and you want the review gate to run standalone, without re-running implement-tasks."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID to review (e.g., ORC-122). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to the orchestrate skill with the review schema. Requires existing design.md/tasks.yaml
and an implementation already on the same change_id (from a prior /design + /implement run) —
does not create a worktree or draft artifacts. A failed review halts rather than looping back
into implement-tasks; fix manually (or re-run `/implement`), then re-invoke `/review`.

```
orchestrate $ARGUMENTS --schema review
```
