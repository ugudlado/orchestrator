---
name: reviewer
description: "Claim and review the next In Review ticket. This skill should be used when the user says 'review', 'review next', 'review next ticket', 'do a review', or wants to process the review queue. Backlog status is the sole router — this skill only touches In Review tickets."
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

This skill is glue; the reviewer agent does the actual review. Backlog status
is the only routing signal — never infer role from anything else.

### 1. Claim the next In Review ticket (atomic)

Run from `$REPO_ROOT` (backlog CLI requires repo-root cwd):

```
backlog task next --status "In Review" --agent "$AGENT_HANDLE"
```

`backlog task next` atomically claims and assigns in one call — no TOCTOU
window. If it reports no ready task, stop and report "review queue empty".

Capture the claimed `TICKET_ID` (e.g. `ORC-44`).

### 2. Resolve the ticket's change directory (strict premise)

Spec/worktree/init are assumed pre-done. Do NOT auto-init.

Scan `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/`, `backlog/`) for the
state whose `change_id` equals the ticket slug OR whose linear/ticket field
matches `TICKET_ID` (case-insensitive). `ticket_id` may be `null` —
fall back to matching `change_id` against the lowercased `TICKET_ID`.

- **No match** → this ticket is not workflow-initialized. Release the claim
  (`backlog task edit <id> -a "" -s "In Review"`), report:
  `Ticket TICKET_ID has no spec/changes/<slug>/state.yaml — not initialized; left in In Review.`
  Stop. (Per strict premise: ideator/architect/human own To Do init.)
- **Match** → record `SLUG`, `STATE_FILE`, and read `flags.worktree`,
  `repo_root` from it. Set `ARTIFACT_DIR`: when `flags.worktree: true` →
  `$WORKTREE_BASE_DIR/$SLUG`, else `$REPO_ROOT/spec/changes/$SLUG`. This is
  where `design.md` / `tasks.md` live (CLAUDE.md § Paths —
  artifacts follow the worktree, state does not).

### 3. Enter the ticket's worktree or branch

- `flags.worktree: true` → `cd "$WORKTREE_BASE_DIR/$SLUG"` (the established
  worktree convention; `WORKTREE_BASE_DIR` defaults to
  `~/code/feature_worktrees`).
- else → `cd "$REPO_ROOT"` and `git checkout` the change's branch.

All subsequent steps run from the resolved working directory.

### 4. Scaffold the resolvr review session

```
resolvr init "$PWD"
```

Idempotent — creates `.review/sessions/<branch>-code.json` +
`.review/AGENTS.md` + `.review/CLAUDE.md` if absent, else reuses. This is the
protocol surface the reviewer agent reads/writes.

### 5. Spawn the reviewer agent

Spawn the `reviewer` agent (model inherits from this session — no model
override). Pass it:

- The full ticket: `backlog task <id> --plain`
- `SLUG`, `STATE_FILE`, `ARTIFACT_DIR`, the resolved working directory
- The design to review against: `$ARTIFACT_DIR/design.md` (read it first —
  it carries both the design and the Acceptance Criteria; the product-level
  what/why is on the ticket). If `design.md` is absent, review against the
  ticket text and note the missing design in the verdict — do not block.
- This instruction:

  > Review this change against `$ARTIFACT_DIR/design.md` and the
  > per-task rubric (Mode 1).
  > Read `.review/AGENTS.md` for the session protocol. Record findings ONLY
  > through these two channels — do not edit production code:
  > 1. **Line-anchored findings** → create resolvr threads in the session
  >    JSON per `.review/AGENTS.md` (severity, anchor, agent message).
  > 2. **Structural rework** → append unchecked tasks to `tasks.md` and add
  >    Implementation Plan subtasks to the ticket via the backlog CLI.
  > End with a verdict line: `VERDICT: APPROVED` or `VERDICT: CHANGES_REQUESTED`.

### 6. Drive the backlog transition

- **`VERDICT: APPROVED`** → `backlog task edit <id> -s "QA Review"`.
  Approval does not mean Done — a separate QA pass (future skill or human)
  moves `QA Review → Done`.
- **`VERDICT: CHANGES_REQUESTED`** → `backlog task edit <id> -s "In Progress"`.
  Same worktree/branch/state.yaml. No follow-up ticket — the resolvr threads
  and unchecked `tasks.md` items ARE the rework. `/developer` re-picks this
  same ticket and treats them like any other unchecked work.

Report the ticket ID, verdict, and transition taken.
