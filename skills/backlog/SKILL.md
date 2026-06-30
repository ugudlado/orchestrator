---
name: backlog
description: >
  Task lifecycle reference for the Backlog.md CLI — how to create, claim, update,
  and complete tasks. Load this whenever you need the exact `backlog task` command
  to file a ticket, change a status, check off acceptance criteria, record
  implementation notes, or mark work done. Covers the create → claim → in-progress
  → done flow agents run every session. For *whether/when* to create a task or how
  to prioritize, that's the /backlog-manager skill's job — this one is the *how*.
user-invocable: true
---

# Backlog.md Task CLI

Everything here uses the globally installed `backlog` binary on task management.
Scope is deliberately narrow: **the task lifecycle**. Board, sequence, milestone,
and cleanup commands exist but aren't covered — run `backlog <cmd> --help` if you
ever need them.

**Run `backlog` from the main repo root, not a feature worktree.** Backlog
resolves its workspace (`~/.config/backlog/workspaces/<name>/`) from the main
checkout; a `git worktree` is not recognized and `backlog` errors with "No
Backlog.md project found". This is expected, not a bug. Workflow ticket-lane
steps (ticket-start/review/qa) `cd "$REPO_ROOT"` first — when a run's `REPO_ROOT`
is a worktree, those `backlog task edit` calls fail with a `WARN ... backlog edit
failed` and the step continues (best-effort). The ticket lane simply won't
advance in backlog for worktree-based runs; update it manually from the main repo.

**Never use `bun run cli`.** It triggers a slow CSS rebuild and hits sandbox write
restrictions on `~/.config/backlog/`, causing silent EPERM failures. The installed
binary handles storage paths (including a redirected `globalStore`) transparently —
so also **never edit task `.md` files directly**; the index and git tracking will
drift out of sync.

```bash
command -v backlog || echo "not installed — run: npm i -g backlog.md"
```

**Cloud sessions only — bypass the sandbox proxy.** When the backlog backend is
remote (`BACKLOG_URL` set), the cloud container's HTTP proxy drops the host and
the CLI fails with "socket connection was closed unexpectedly". Unset the proxy
for backlog commands — the CLI's only upstream is the backlog host, so this is safe:

```bash
https_proxy="" http_proxy="" HTTPS_PROXY="" HTTP_PROXY="" backlog task list --plain
```

Prefix **every** `backlog` command this way in a cloud session. (Local sessions need
no prefix.) `NO_PROXY` does **not** work here — the sandbox enforces the proxy at a
level that ignores `NO_PROXY`, so unsetting the proxy vars inline is the only way.

Add `--plain` to any read command (`list`, `view`, `search`) for scriptable text
instead of the interactive TUI. In an agent context you almost always want `--plain`.

---

## The lifecycle at a glance

A task moves through statuses. The exact lane names come from the project's
`backlog/config.yml` — read them first so you transition to a status that exists:

```bash
backlog config get statuses     # e.g. [Backlog, Ready, To Do, In Progress, Done]
```

Typical flow an agent runs:

```bash
backlog task next --plain --agent @me     # 1. atomically claim the next Ready task
backlog task edit <id> -s "In Progress"   # 2. mark it started
# ...do the work, checking off criteria as you go...
backlog task edit <id> --check-ac 1 --check-ac 2
backlog task edit <id> -s Done --append-final-summary "What shipped and where"
```

Never invent a task ID. Resolve it from `list`, `search`, or the output of `next`.

---

## Claiming work

`next` is atomic — it picks one task from a status lane and (optionally) assigns it,
so two agents running in parallel won't grab the same one.

```bash
backlog task next --plain                 # claim next from "Ready" (default lane)
backlog task next --plain --agent @me     # claim AND assign to @me
backlog task next --plain --status "To Do"  # pick from a different lane
```

If `next` returns nothing, the lane is empty — that's the signal there's no ready
work, not an error.

---

## Creating a task

Minimum is a title. Everything else is optional but a well-formed task carries its
own context so it's resumable across sessions and pickable by another agent.

```bash
backlog task create "Title" --priority high
```

Full example with the fields that actually matter for handoff:

```bash
backlog task create "Add streak counter to daily challenge" \
  --priority high \
  -d "Persist a per-user streak in localStorage; increment on a same-day win,
reset on a missed day. Read by the results screen." \
  --ac "Streak increments on first win of the day" \
  --ac "Streak resets to 0 after a missed calendar day" \
  --ac "Survives a page reload" \
  --plan "1. storage adapter  2. streak reducer  3. wire results screen" \
  -l feature,retention
```

Useful create options (run `backlog task create --help` for the full set):

| Option                         | Purpose                                                        |
| ------------------------------ | -------------------------------------------------------------- |
| `--priority high\|medium\|low` | Priority. Default is medium if omitted.                        |
| `-d, --desc <text>`            | Description. Multi-line — put real newlines inside the quotes. |
| `--ac <text>`                  | Add an acceptance criterion. Repeat for several.               |
| `--plan <text>`                | Implementation plan.                                           |
| `--notes <text>`               | Implementation notes.                                          |
| `-l, --labels a,b`             | Labels (comma-separated).                                      |
| `-s, --status <name>`          | Start in a non-default status.                                 |
| `--depends-on <ids>`           | Block this task on others (comma-separated).                   |
| `-p, --parent <id>`            | Make it a subtask.                                             |

`create` prints the new ID — capture it for the follow-up edits.

---

## Editing & status transitions

`edit` is how every state change happens. The taskId is required; everything else
is the change you're making.

```bash
backlog task edit SPOT-2 -s "In Progress"          # transition status
backlog task edit SPOT-2 -a @me                     # assign
backlog task edit SPOT-2 --priority high            # reprioritize
backlog task edit SPOT-2 -t "Clearer title"         # rename
```

### Acceptance criteria & Definition of Done

Criteria are 1-indexed. Check them off as you complete them so the task reflects
real progress, not just a final flip to Done.

```bash
backlog task edit SPOT-2 --ac "New criterion added mid-flight"
backlog task edit SPOT-2 --check-ac 1 --check-ac 2   # mark #1 and #2 done
backlog task edit SPOT-2 --uncheck-ac 3              # reopen #3
backlog task edit SPOT-2 --remove-ac 4               # delete #4
backlog task edit SPOT-2 --check-dod 1               # same verbs for DoD items
```

### Notes & summary — append, don't clobber

`--notes` and `--final-summary` _replace_ existing content. To add to it across a
multi-step task without losing earlier context, use the append variants:

```bash
backlog task edit SPOT-2 --append-notes "Hit a CORS issue; fixed via proxy"
backlog task edit SPOT-2 --append-final-summary "Shipped in commit abc1234"
```

### Finishing a task

Mark Done and leave a summary in the same call so the record is self-contained:

```bash
backlog task edit SPOT-2 -s Done \
  --append-final-summary "Streak persists in localStorage; see streakStore.ts. Commit abc1234."
```

If a task is fully finished and you want it out of the active list, archive it
(the orchestrator usually handles this — don't archive unless asked):

```bash
backlog task archive SPOT-2
```

---

## Finding tasks

```bash
backlog task list --plain                       # all tasks, grouped by status
backlog task list --plain -s "In Progress"      # filter by status
backlog task list --plain --priority high       # filter by priority
backlog task list --plain -a @me                # only mine
backlog task view SPOT-2 --plain                # full detail of one task
backlog search "streak" --plain                 # full-text search across tasks
backlog search "router" --plain --status Done   # search + filter
```

Always `search` before creating to avoid filing a duplicate of existing work.

---

## Config (quick reference)

Two layers. You rarely write these — mostly you read `statuses` to know the valid
lane names before a transition.

| Layer   | Location                       | Controls                                      |
| ------- | ------------------------------ | --------------------------------------------- |
| Project | `backlog/config.yml` (in repo) | task prefix, statuses, labels                 |
| Machine | `~/.config/backlog/config.yml` | `globalStore`, `backlog_url`, `backlog_token` |

Remote server (CLI + MCP share this):

```yaml
# ~/.config/backlog/config.yml
globalStore: ~/.config/backlog/workspaces
backlog_url: http://your-server:6420
backlog_token: your-secret-token # optional
```

Env vars `BACKLOG_URL` and `BACKLOG_TOKEN` override config when set.

---

## MCP

When the backlog MCP server is active, prefer MCP tools for **reads** (structured
data, no parsing). Use the **CLI for writes** so git tracking stays in sync.

- Resource: `backlog://workflow/overview` — full MCP tool reference
- Tools follow `backlog.<operation>` (e.g. `backlog.list_tasks`, `backlog.get_task`)
