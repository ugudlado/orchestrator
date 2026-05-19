---
name: developer
description: "Claim and implement the next ready ticket. This skill should be used when the user says 'develop', 'develop next', 'work next ticket', 'implement next', or wants to process the development queue. Backlog status is the sole router — it claims In Progress tickets first (kicked-back rework), then falls back to Ready tickets (fresh work), promoting them to In Progress on claim."
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
special rework path; it just sorts ahead of fresh Ready work.

### 1. Claim the next ticket via `/backlog-manager`

The developer skill does not know or care which ticketing backend the repo
uses — that's `/backlog-manager`'s job (it detects the backend from
`ticketing:` in `spec/project.yaml` and runs the right commands). Load
`/backlog-manager` and ask it to atomically claim the next ticket for
`$AGENT_HANDLE`, applying this policy:

1. **In Progress first** — kicked-back rework that's already mid-flight.
2. **Then Ready** — fresh work; the claim must leave the ticket In Progress
   so the rest of the workflow and the next `/developer` run see consistent
   state.

The claim must be atomic (claim + assign in one step — no TOCTOU window);
`/backlog-manager` maps that to the backend's mechanism. If both queues are
empty, stop and report "development queue empty". Capture `TICKET_ID`.

### 2. Resolve the ticket's change directory (strict premise)

Spec/worktree/init are assumed pre-done. Do NOT auto-init.

Scan `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/`, `backlog/`) for the
state whose `change_id` equals the ticket slug OR whose linear/ticket field
matches `TICKET_ID` (case-insensitive). `linear_ticket_id` may be `null` —
fall back to matching `change_id` against the lowercased `TICKET_ID`.

- **No match** → not workflow-initialized. Ask `/backlog-manager` to release
  the claim (unassign, leave status In Progress), report:
  `Ticket TICKET_ID has no spec/changes/<slug>/state.yaml — not initialized; left in In Progress.`
  Stop.
- **Match** → record `SLUG`, `STATE_FILE`, read `flags.worktree`,
  `repo_root`. Set `ARTIFACT_DIR`: when `flags.worktree: true` →
  `$WORKTREE_BASE_DIR/$SLUG`, else `$REPO_ROOT/spec/changes/$SLUG`. This is
  where `design.md` / `tasks.md` live (CLAUDE.md § Paths —
  artifacts follow the worktree, state does not).

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

- The full ticket body (fetch it via `/backlog-manager` for the detected
  backend — e.g. plain-text issue/task contents)
- `SLUG`, `STATE_FILE`, `ARTIFACT_DIR`, the resolved working directory
- The design to implement against: `$ARTIFACT_DIR/design.md` (read it
  before coding — it carries both the design and the Acceptance Criteria;
  the product-level what/why is on the ticket). If `design.md` is absent,
  implement against the ticket text and flag the missing design in the
  result — do not block.
- If a resolvr session exists: its path + `.review/AGENTS.md`, with the
  instruction to resolve every open thread via the documented protocol
  before completing.
- The standing instruction to drive its work through the orchestrator step
  loop (see step 6 below) and self-verify with evidence to 9/10.

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
self-verification passes, transition the ticket to Code Review via
`/backlog-manager` (it maps the move to the detected backend). Report the
ticket ID and that it is ready for `/reviewer`.
