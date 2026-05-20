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

Glue skill: `/backlog-manager` for tickets, orchestrator artifacts for workflow context, `agents/reviewer.md` for review standards.

### 1. Claim work via `/backlog-manager`

Load `/backlog-manager` and atomically claim the next `Code Review` ticket for `$AGENT_HANDLE`.

If the queue is empty, stop and report `review queue empty` (nothing to review). Capture `TICKET_ID`.

### 2. Resolve workflow state

Spec/worktree/init assumed done — do not auto-init (same as `/developer`; init is upstream).

Find `$WORKFLOW_STATE_DIR/*/state.yaml` (skip `archive/`, `backlog/`) by `change_id`, `ticket_id`, or lowercased ticket slug.

- No match: note on ticket that workflow is not initialized; leave `Code Review`; stop.
- Match: record `SLUG`, `STATE_FILE`, `ARTIFACT_DIR`, working directory. Artifacts at `$WORKTREE_BASE_DIR/$SLUG` when `flags.worktree: true`, else `$REPO_ROOT/spec/changes/$SLUG`.

### 3. Run review

Reviewer verifies unchecked `tasks.md` items, `Verify:` lines, project `verify_commands`, and design AC. Driver does not perform these checks.

Run `resolvr init "$PWD"` from the working directory, then spawn `reviewer` with: full ticket body; `STATE_FILE`, `ARTIFACT_DIR`, working directory; `design.md`, `tasks.md` when present; `.review/AGENTS.md` and review session path.

Reviewer must not edit production code.

### 4. Record blocking findings

For every approval-blocking issue, append an unchecked `tasks.md` item with: exact code references, what is wrong, why it matters, what should improve, and how to verify the fix. Line-anchored findings also go in resolvr. Non-blocking suggestions stay in the review report only.

### 5. Transition the ticket

Via `/backlog-manager`:

- `VERDICT: APPROVED` → `QA Review`
- `VERDICT: CHANGES_REQUESTED` → `In Progress`

Do not move to `Done`. Report ticket ID, verdict, and transition.
