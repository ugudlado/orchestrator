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

Glue skill: `/backlog-manager` for tickets, orchestrator for workflow, `agents/developer.md` for implementation. State updates follow `config/steps/contracts/done-payload.md` — driver calls `orchestrator done` after COMPLETION; agents MUST NOT edit `state.yaml`. Driver does not verify `tasks.md` or run verify commands; `/reviewer` does.

### 1. Claim work via `/backlog-manager`

Load `/backlog-manager` and atomically claim the next ticket for `$AGENT_HANDLE`:

1. `In Progress` first (active work, including code-review rework).
2. If none, claim `Ready` — move to `In Progress` before coding.

If both queues are empty, stop and report `development queue empty` (no ticket to implement). Capture `TICKET_ID`.

### 2. Resolve workflow state

Spec/worktree/init assumed done — do not auto-init (init is `/orchestrate` or seed-state.sh, not this skill).

Find `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/`, `backlog/`) by `change_id`, `ticket_id`, or lowercased ticket slug.

- No match: note on ticket that workflow is not initialized; leave `In Progress`; stop.
- Match: record `SLUG`, `STATE_FILE`, `ARTIFACT_DIR`, working directory. Artifacts at `$WORKTREE_BASE_DIR/$SLUG` when `flags.worktree: true`, else `$REPO_ROOT/spec/changes/$SLUG`.

### 3. Run implementation (orchestrator loop)

Developer agent completes **all** unchecked `tasks.md` items in one spawn when possible, then returns COMPLETION. Driver only orchestrates:

```
orchestrator next "$STATE_FILE"
# spawn developer (execute-next-task)

orchestrator done "$STATE_FILE" <<< '<json from COMPLETION + dispatch context>'
orchestrator next "$STATE_FILE"
```

**Happy path** (all tasks finished in one spawn): one `done`, then `next` advances to `run-phase-review`.

**Partial path** (blocked, retry, or escalation before all tasks `[x]`): agent returns COMPLETION early; driver still calls `done` then `next`. `repeat_until: all_tasks_completed` re-dispatches `execute-next-task` until every task is `[x]` or the workflow is blocked (exit 2).

Do not inspect `tasks.md` or re-run verify commands between `done` and `next`.

Spawn `developer` with: full ticket body; `STATE_FILE`, `ARTIFACT_DIR`, working directory; `discovery.md`, `spec.md`, `design.md`, `tasks.md` when present; `.review/AGENTS.md` and review session path when present.

Unchecked `tasks.md` items are the work queue (including reviewer-added code-review comments). No separate rework mode — rework is done when those items are implemented, verified, and checked.

### 4. Hand off to Code Review

When `orchestrator next` advances past `execute-next-task` (implement phase review step dispatched or workflow complete), move ticket to `Code Review` via `/backlog-manager`.

Do not move to `QA Review` or `Done`. Task completion and test evidence are verified by `run-phase-review` and `/reviewer`, not this driver loop.
