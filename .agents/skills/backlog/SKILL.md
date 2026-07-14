---
name: backlog
description: Manage tasks, plans, and project state in Backlog.md through its MCP tools (interactive agent work) and REST API (scripts/automation). Use when the user asks to create, list, view, or edit a backlog task, manage the backlog, or work tickets. There is no CLI — never shell out to `backlog` or hand-edit task files.
---

# backlog

Manage tasks in a repo where **Backlog.md** is the task system. Backlog is reached
two ways — there is **no CLI**:

- **MCP tools** — the interface for interactive agent work (`task_list`,
  `task_view`, `task_create`, `task_edit`, `task_next`).
- **REST API** — the same operations over HTTP, for scripts/automation
  (workflow step scripts already use this via `config/steps/lib/backlog-api.sh`).

## Golden rules

- **Never shell out to a `backlog` CLI** — it does not exist. **Never hand-edit
  task `.md` files** — that breaks metadata sync. All writes go through the MCP
  tools (or REST).
- **Every call needs a project.** A task display id (e.g. `BKG-541`) is unique
  only *within* a project, so a call with no project is rejected (HTTP 400). See
  [Project resolution](#project-resolution).
- **Read before you write.** `task_view` before editing; claim a task before
  working it.

## Project resolution

Resolve the project once and pass it on every call. Precedence (first non-empty):

1. `BACKLOG_PROJECT` / `BACKLOG_PROJECT_ID` — env override (id, guid, or name).
2. `backlog_project` in the repo's `spec/project.yaml` — the per-repo default.

This is what makes one Backlog server + token work across repos: the URL/token
are global; the *project* comes from each repo's config. Pass it explicitly as
the `project` argument on MCP tools (`{"project": "<id>", ...}`) — do not rely on
the connection's ambient default, which may point at a different project.

## MCP tools

| Tool | Purpose | Key args (besides `project`) |
| --- | --- | --- |
| `task_list` | List/filter tasks; also how you see the board + status lanes in use | `status`, `assignee`, `labels`, `search`, `limit` |
| `task_view` | Read one task | `id` (required) |
| `task_create` | Create a task | `title` (required), `description`, `status`, `priority`, `labels`, `assignee`, `acceptanceCriteria` |
| `task_edit` | Change any field | `id` (required) + fields below |
| `task_next` | Atomically claim the next ready task | `agent`; (optional) `status`, `taskId` |

For a plain "claim the next ready task," call `task_next` with just `project` +
`agent` — **omit `status` and `taskId`** (it defaults to the project's ready lane).
`status` only selects a *different* lane to claim *from*; `taskId` claims one
specific task. Don't invent a `taskId`.

### `task_edit` — arrays, not repeated flags

Metadata is set directly: `status`, `priority`, `milestone` are strings. A task
has a **single assignee**, but the `assignee` field is **array-typed** (the server
rejects a bare string) — pass a one-element array: `assignee: ["@you"]`. `labels`
is likewise an array. Rich fields also use **arrays** (one element per line/item) —
there is no `\n` or repeated-flag gotcha:

- Acceptance criteria: `acceptanceCriteriaCheck: [1, 2]`, `acceptanceCriteriaUncheck`,
  `acceptanceCriteriaAdd: ["New outcome"]`, `acceptanceCriteriaRemove`,
  `acceptanceCriteriaSet`. Indices are 1-based against the task's AC list.
- Notes: `notesAppend: ["line 1", "line 2"]` (or `notesSet`, `notesClear`).
- Plan: `planSet: "..."` / `planAppend: [...]` / `planClear`.
- Final summary: `finalSummary: "..."` (PR-style).
- Definition of Done: `definitionOfDoneCheck: [1]`, `definitionOfDoneAdd`, etc.

## Workflow

1. `task_list` — see the board and the status lanes in use; filter by `status` to
   find work (or `task_next` to claim the next ready task).
2. `task_view` — read the task fully (description, ACs, refs).
3. `task_edit` — **claim first**: set `status` (a real lane name) + `assignee: ["@you"]`.
4. `task_edit` `planSet` — add the plan; share it with the user before coding.
5. Implement; `task_edit` `acceptanceCriteriaCheck` and `notesAppend` as you go.
6. `task_edit` `finalSummary` — PR-style wrap-up.
7. `task_edit` `status: "Done"` — only when every AC and DoD is checked, the final
   summary is set, and tests/docs/review pass.

**Good acceptance criteria are outcomes, not steps:** "User can log in with valid
credentials" ✓; "Add handleLogin() in auth.ts" ✗. Implement only what an AC states —
to do more, add an AC (`acceptanceCriteriaAdd`) or create a follow-up task first.

## REST API (scripts / automation)

Same operations over HTTP; auth via `BACKLOG_URL` + `BACKLOG_TOKEN` and the resolved
project as a `?project=<id>` query. Used by workflow step scripts through
`config/steps/lib/backlog-api.sh` (`backlog_api_get_task`, `backlog_api_put_status`).

| Method & path | Purpose |
| --- | --- |
| `GET /api/tasks?project=<id>` | List tasks (filters as query params) |
| `GET /api/tasks/:id?project=<id>` | View one task |
| `POST /api/tasks?project=<id>` | Create (`{"title": ..., "description": ...}`) |
| `PUT /api/tasks/:id?project=<id>` | Partial update (`{"status": "In Progress"}`) |
| `DELETE /api/tasks/:id?project=<id>` | Delete |

```bash
curl -fsS -H "Authorization: Bearer $BACKLOG_TOKEN" \
  "$BACKLOG_URL/api/tasks/BKG-1?project=$BACKLOG_PROJECT_ID"
```

The **MCP HTTP endpoint** for the same server is
`${BACKLOG_URL}/projects/${BACKLOG_PROJECT_ID}/mcp` (or `${BACKLOG_URL}/mcp?project=<id>`),
bearer-authenticated — the project is required there too.

## Gotchas

- **No CLI, no file edits.** If a change "won't take," you are reaching for a CLI
  or editing a file — use a tool/REST call instead.
- **Status values are project-defined.** Use a lane name already in use (seen via
  `task_list`) — don't invent one; an unknown status is rejected.
- **`assignee` and `labels` are arrays.** One assignee → `["@you"]` (a bare string
  is rejected).
- **AC/DoD indices are 1-based** and operate on arrays: `acceptanceCriteriaCheck: [1, 2]`.
- **Task images** live under `backlog/assets/`; reference as `![alt](assets/images/foo.png)`.

## Task shape (read-only reference)

`task_view` returns fields like: `id`, `title`, `status`, `priority`, `labels`,
`assignee`, `description`, and `acceptanceCriteriaItems`
(`[{index, checked, text}]`). Treat this as data to read — never write it back as a file.
