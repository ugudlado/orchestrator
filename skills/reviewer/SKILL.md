---
name: reviewer
description: "Claim and review the next Code Review ticket. This skill should be used when the user says 'review', 'review next', 'review next ticket', 'do a review', or wants to process the review queue. Backlog status is the sole router — this skill only touches Code Review tickets."
user-invocable: true
args:
  - name: agent-handle
    description: "Backlog agent handle for the atomic claim (default: @reviewer)."
    required: false
---

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}
WORKTREE_BASE_DIR=${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}
AGENT_HANDLE=${1:-@reviewer}
```

## Execution

This skill is glue; the reviewer agent does the actual review. Keep ticket
operations in `/backlog-manager`, workflow context in the orchestrator
artifacts, and review standards in `agents/reviewer.md`.

### 1. Claim work via `/backlog-manager`

Load `/backlog-manager` and atomically claim the next `Code Review` ticket
for `$AGENT_HANDLE`.

If the queue is empty, stop and report `review queue empty`. Capture
`TICKET_ID`.

### 2. Resolve workflow state

Spec/worktree/init are assumed pre-done. Do not auto-init.

Find the matching `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/` and
`backlog/`) by `change_id`, `ticket_id`, or lowercased ticket slug.

- No match: append a ticket note that the workflow is not initialized, leave
  the ticket in `Code Review`, and stop.
- Match: record `SLUG`, `STATE_FILE`, `ARTIFACT_DIR`, and working directory.
  Artifacts live at `$WORKTREE_BASE_DIR/$SLUG` when `flags.worktree: true`,
  otherwise `$REPO_ROOT/spec/changes/$SLUG`.

### 3. Run review

Run `resolvr init "$PWD"` from the resolved working directory, then spawn the
`reviewer` agent with:

- full ticket body
- `STATE_FILE`, `ARTIFACT_DIR`, and working directory
- `design.md` and `tasks.md` paths when present
- `.review/AGENTS.md` and the review session path

The reviewer must not edit production code.

### 4. Record blocking findings

For every approval-blocking issue, the reviewer must append an unchecked
`tasks.md` item with:

- exact code references
- what is wrong
- why it matters
- what should be improved
- how the developer verifies the fix

Line-anchored findings should also be recorded in resolvr. Non-blocking
suggestions stay in the review report and do not become `tasks.md` items.

### 5. Transition the ticket

Drive the transition through `/backlog-manager`:

- `VERDICT: APPROVED` -> move to `QA Review`
- `VERDICT: CHANGES_REQUESTED` -> move to `In Progress`

Do not move review-owned tickets to `Done`.

Report the ticket ID, verdict, and transition taken.
