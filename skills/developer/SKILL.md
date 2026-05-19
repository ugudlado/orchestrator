---
name: developer
description: "Claim and implement the next development ticket. This skill should be used when the user says 'develop', 'develop next', 'work next ticket', 'implement next', or wants to process the development queue. Backlog status is the router: claim In Progress rework first, then Ready work; completed implementation moves to Code Review."
user-invocable: true
args:
  - name: agent-handle
    description: "Backlog agent handle for the atomic claim (default: @developer)."
    required: false
---

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}
WORKTREE_BASE_DIR=${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}
AGENT_HANDLE=${1:-@developer}
```

## Execution

This skill is glue; the developer agent writes the code. Keep ticket
operations in `/backlog-manager`, workflow progression in the orchestrator,
and implementation work in `agents/developer.md`.

### 1. Claim work via `/backlog-manager`

Load `/backlog-manager` and atomically claim the next ticket for
`$AGENT_HANDLE`:

1. Claim `In Progress` first. These are active implementation tickets,
   including code-review rework.
2. If none exist, claim `Ready`.
3. Fresh `Ready` claims must be moved to `In Progress` before coding.

If both queues are empty, stop and report `development queue empty`. Capture
`TICKET_ID`.

### 2. Resolve workflow state

Spec/worktree/init are assumed pre-done. Do not auto-init.

Find the matching `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/` and
`backlog/`) by `change_id`, `ticket_id`, or lowercased ticket slug.

- No match: append a ticket note that the workflow is not initialized, leave
  the ticket in `In Progress`, and stop.
- Match: record `SLUG`, `STATE_FILE`, `ARTIFACT_DIR`, and working directory.
  Artifacts live at `$WORKTREE_BASE_DIR/$SLUG` when `flags.worktree: true`,
  otherwise `$REPO_ROOT/spec/changes/$SLUG`.

### 3. Run implementation

Spawn the `developer` agent with:

- full ticket body
- `STATE_FILE`, `ARTIFACT_DIR`, and working directory
- `discovery.md`, `spec.md`, `design.md`, and `tasks.md` paths when present
- `.review/AGENTS.md` and the review session path when present

The developer agent must treat unchecked `tasks.md` items as the work queue.
That includes reviewer-added code-review comments. There is no separate
rework mode: code-review rework is complete only when the corresponding
unchecked `tasks.md` items are implemented, verified, and checked.

Advance only through the orchestrator loop:

```
orchestrator next "$STATE_FILE"
# execute the returned step
orchestrator done "$STATE_FILE"
```

Repeat until `execute-next-task` reports `all_tasks_completed`.

### 4. Hand off to Code Review

Before handoff, verify:

- no unchecked `tasks.md` items remain, except explicitly quarantined items
- reviewer-added rework tasks are resolved
- relevant tests/checks were run with evidence
- orchestrator state was updated via `orchestrator done`

Then transition the ticket to `Code Review` via `/backlog-manager`. Do not
move developer-owned tickets to `QA Review` or `Done`.
