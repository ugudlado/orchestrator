---
name: backlog-manager
description: >
  Load this skill whenever dealing with tasks, tickets, or project management — creating,
  editing, listing, claiming, prioritizing, or closing work items. Use when the user says
  "create a task", "file a ticket", "what should we work on", "mark this done", "track this",
  "what's next", or when any workflow step interacts with the task backlog. This skill
  auto-detects the ticketing backend (Linear or Backlog.md) per repo and delegates accordingly.
user-invocable: true
---

# Backlog Manager

Handles the _what_ and _when_ of task management. Detects the correct ticketing backend
per repo and delegates to the right skill for commands.

---

## Backend detection

Read `ticketing:` from the repo's `spec/project.yaml` — this is the authoritative source:

```bash
grep "^ticketing:" spec/project.yaml | awk '{print $2}'
```

| Value     | Backend                  | Load skill |
| --------- | ------------------------ | ---------- |
| `backlog` | Backlog.md global binary | `/backlog` |
| `linear`  | Linear MCP server        | `/linear`  |

If `spec/project.yaml` is missing or `ticketing:` is not set, default to `backlog` and warn the user to configure it.

Load the detected backend skill before executing any commands. Never mix backends in one operation.

### Linear context (when ticketing: linear)

All Linear IDs are stored in `spec/project.yaml` under the `linear:` key:

```yaml
linear:
  team_id: <uuid>
  team_prefix: HL
  project_id: <uuid>
  product_label_id: <uuid> # repo-specific product label
```

Pass these directly to `/linear` MCP tool calls — no additional config lookup needed.

---

## When to create a task

Create a task when:

- Work spans more than one session or needs tracking across time
- Multiple steps are involved and progress needs to be resumable
- The work is a discrete deliverable (feature, bugfix)
- An agent needs to pick it up autonomously

Skip task creation for:

- Quick one-off fixes completed in the same session
- Exploratory questions with no output artifact
- Work already covered by an existing open task

Always search first to avoid duplicates — use the backend's search command before creating.

---

## Operations

The operations below are the contract other skills (notably `/developer`
and `/reviewer`) delegate here. They name _what_ to do; you resolve the
backend (above), load its skill (`/backlog` or `/linear`), and run the
mapped command. The semantics must hold identically on both backends —
only the commands differ.

| Operation                | Backlog.md                                                                       | Linear                                                                                       |
| ------------------------ | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **Search**               | `backlog search "<q>" --plain`                                                   | `/linear` search tools                                                                       |
| **Create**               | `backlog task create "Title" --priority <p>`                                     | `save_issue` via `/linear`                                                                   |
| **Claim next by status** | `backlog task next --status "<S>" --agent @<h>`                                  | pick the top unassigned issue in state `<S>` via `/linear` list, then `save_issue` to assign |
| **Release claim**        | `backlog task edit <id> -a "" -s "<S>"`                                          | `save_issue` clearing `assignee`, leaving state `<S>`                                        |
| **Transition status**    | `backlog task edit <id> -s "<S>"`                                                | `save_issue` with the `stateId` for `<S>` (resolve via `list_issue_statuses`)                |
| **Fetch body**           | MCP `task_view` / REST `GET /api/tasks/:id` (engine: `load-ticket-context` step) | `get_issue { id }` via `/linear`                                                             |
| **Archive**              | `backlog task archive <id>`                                                      | close/cancel via `/linear`                                                                   |

### Atomicity of "claim next by status"

A claim must select one ticket from a status lane **and** assign it to the
agent in a single step, so two agents claiming in parallel never grab the
same ticket (no TOCTOU window).

- **Backlog.md** — `backlog task next --status "<S>" --agent @<h>` is
  atomic by design. Use it; never emulate it with `list` + `edit`.
- **Linear** — there is no atomic claim primitive. Approximate it: list
  unassigned issues in state `<S>` ordered oldest-first, then immediately
  `save_issue` to set the assignee on the top one. Re-fetch and verify the
  assignee is the intended agent before proceeding; if it changed under
  you, retry with the next issue.

### Claim-then-promote (Ready → In Progress)

`/developer` claims from `In Progress` first, then falls back to a
`Ready`-equivalent lane. A ticket claimed from a not-yet-started lane must
be moved to the in-progress state as part of the claim, so the rest of the
workflow sees consistent state. Sequence: _claim next by status_ on the
fallback lane, then _transition status_ to the in-progress lane.

> Resolve the exact lane names before transitioning — they are per-project.
> Backlog.md: `backlog config get statuses`. Linear:
> `list_issue_statuses`. Never transition to a status string that isn't in
> that list.

---

## Prioritization

- **High** — blocks other work, user-facing breakage, or time-sensitive
- **Medium** — default for new features and improvements
- **Low** — nice-to-have, no urgency

Move tasks to **Ready** (Backlog.md) or **In Progress** (Linear) only when dependencies
are resolved and the task is fully specified.

---

## Workflow integration (orchestrator)

**Dispatch loop (`orchestrator_next/run_loop.py`):** Ticket lane changes are **not** agent work.
Dedicated workflow steps (`ticket-start`, `ticket-review`, `ticket-qa`) push outbound
lane changes via the backlog CLI.
Do not call this skill for status transitions when the shell loop is driving the workflow.

**LLM dispatch (`skills/orchestrate/SKILL.md`) or queue skills (`/developer`, `/reviewer`):**

1. **Detect backend** (above) before any operation
2. **Start of run** — _claim next by status_ for the status the calling
   skill asks for (the caller owns the queue policy; e.g. `/developer`
   claims In Progress then a Ready lane, `/reviewer` claims Code Review).
3. **Mid-task** — note blockers in the task description (no lane change if shell loop active)
4. **After merge** — transition to Done when not using shell loop; archive step handles the rest
5. **New observations** — go into commit footer or a new task, not the current task's files

---

## Rules

- Detect the backend first — never assume.
- Never guess task IDs. Resolve via list/search commands.
- Never edit task files directly — always go through the CLI or MCP.
- One task = one concern. Split if a task has grown to cover two things.
- Ready = fully specified + unblocked. Don't move tasks to Ready prematurely.
- A claim is atomic: select-from-lane + assign in one step. On Backlog.md
  use `task next`; never emulate it with list+edit. On Linear, claim then
  verify the assignee stuck before proceeding.
- Resolve real lane names (`backlog config get statuses` /
  `list_issue_statuses`) before any transition — never pass a status string
  that isn't in that list.
