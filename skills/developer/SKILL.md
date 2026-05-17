---
name: developer
description: "Claim and implement the next In Progress ticket. This skill should be used when the user says 'develop', 'develop next', 'work next ticket', 'implement next', or wants to process the development queue. Backlog status is the sole router — this skill only touches In Progress tickets, including ones kicked back by review."
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

This skill is glue; the developer agent writes the code. Backlog status is
the only routing signal. A kicked-back ticket is just an In Progress ticket
with extra unchecked `tasks.md` items and open resolvr threads — there is no
special rework path.

### 1. Claim the next In Progress ticket (atomic)

Run from `$REPO_ROOT`:

```
backlog task next --status "In Progress" --agent "$AGENT_HANDLE"
```

Atomic claim + assign — no TOCTOU window. If no ready task, stop and report
"development queue empty". Capture `TICKET_ID`.

### 2. Resolve the ticket's change directory (strict premise)

Spec/worktree/init are assumed pre-done. Do NOT auto-init.

Scan `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/`, `backlog/`) for the
state whose `change_id` equals the ticket slug OR whose linear/ticket field
matches `TICKET_ID` (case-insensitive). `linear_ticket_id` may be `null` —
fall back to matching `change_id` against the lowercased `TICKET_ID`.

- **No match** → not workflow-initialized. Release the claim
  (`backlog task edit <id> -a "" -s "In Progress"`), report:
  `Ticket TICKET_ID has no spec/changes/<slug>/state.yaml — not initialized; left in In Progress.`
  Stop.
- **Match** → record `SLUG`, `STATE_FILE`, read `flags.worktree`,
  `repo_root`.

### 3. Enter the ticket's worktree or branch

- `flags.worktree: true` → `cd "$WORKTREE_BASE_DIR/$SLUG"` (defaults to
  `~/code/feature_worktrees/<slug>`).
- else → `cd "$REPO_ROOT"` and `git checkout` the change's branch.

### 4. Detect prior review feedback

If `.review/sessions/<branch>-code.json` exists with open threads, this
ticket was kicked back. The developer agent must resolve those threads per
`.review/AGENTS.md` (apply fix → set thread `status: resolved` → add agent
message) AND clear the unchecked `tasks.md` items the reviewer added. If no
session or no open threads, it's a fresh implementation pass.

### 5. Spawn the developer agent

Spawn the `developer` agent (model inherits from this session — no model
override). Pass it:

- The full ticket: `backlog task <id> --plain`
- `SLUG`, `STATE_FILE`, the resolved working directory
- If a resolvr session exists: its path + `.review/AGENTS.md`, with the
  instruction to resolve every open thread via the documented protocol
  before completing.
- The standing instruction to drive its work through the orchestrator step
  loop (see step 6) and self-verify with evidence to 9/10.

### 6. Step loop

The developer agent advances the workflow via the orchestrator CLI — never
edit `state.yaml` directly (see CLAUDE.md § Repo Wiring):

```
orchestrator next "$STATE_FILE"      # get next step
# ... agent executes the step ...
orchestrator done "$STATE_FILE"      # JSON payload on stdin
```

Repeat until `execute-next-task` reports all `tasks.md` items checked
(`repeat_until: all_tasks_completed`).

### 7. Hand back to review

When all tasks (including any reviewer-added rework items) are checked and
self-verification passes, move the ticket to In Review:

```
backlog task edit <id> -s "In Review"
```

Report the ticket ID and that it is ready for `/reviewer`.
