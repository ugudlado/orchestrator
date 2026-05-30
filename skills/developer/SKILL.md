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

Glue skill: `/backlog-manager` for tickets, orchestrator for workflow, `agents/developer.md` for implementation. Driver calls `orchestrator done` after COMPLETION; agents MUST NOT edit `state.yaml`. Driver does not verify `tasks.yaml` or run verify commands; `/reviewer` does.

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

ORC-65: tasks are first-class DAG nodes (`task-T-N`) in `workflow_plan[implement].nodes`. Each task-node is a separate `execute-one-task` developer spawn. The driver loop is unchanged — `orchestrator next` / `done` / `next` repeats until `run-phase-review` is dispatched.

```
orchestrator next "$STATE_FILE"
# spawn developer (execute-one-task for task-T-N)

orchestrator done "$STATE_FILE" <<< '<json from COMPLETION + dispatch context>'
orchestrator next "$STATE_FILE"
# returns next ready task-node, or run-phase-review when all tasks complete
```

**One task per spawn**: each `execute-one-task` spawn implements exactly one task and returns COMPLETION. The dispatcher advances to the next ready task-node automatically.

**Rework**: when `run-phase-review` returns `needs_work`, the agent appends fix entries to `tasks.yaml` and calls `orchestrator expand-plan` before COMPLETION. The driver loop continues — `orchestrator next` returns the first fix task-node.

Do not inspect `tasks.yaml` or re-run verify commands between `done` and `next`.

Spawn `developer` with: full ticket body; `STATE_FILE`, `ARTIFACT_DIR`, working directory; `discovery.md`, `design.md`, `tasks.yaml` when present; `.review/AGENTS.md` and review session path when present.

Each `step_context.task` carries the full task payload (id, title, files, verify, test_scenarios). The agent implements that one task and returns COMPLETION — no task scanning, no loop.

### 4. Ticket status (shell loop vs this skill)

When the workflow runs via `scripts/run-workflow.sh`, **do not** transition ticket lanes with `/backlog-manager` — dedicated workflow steps (`ticket-start`, `ticket-review`, `ticket-qa`) update the ticket via the backlog CLI at the right transitions, and `ticket-reconcile.sh` polls for external rework before the next dispatch. Agents only implement tasks; ticket updates are out of scope.

When using this skill **without** the shell loop (manual `/developer` queue driver), still use `/backlog-manager` for claim and lane changes as documented in steps 1–2.

Do not move tickets to `Done` from this skill. Task completion and test evidence are verified by `run-phase-review` and `/reviewer`.
