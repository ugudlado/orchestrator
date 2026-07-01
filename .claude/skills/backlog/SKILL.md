---
name: backlog
description: This skill should be used when the user asks to "create a backlog task", "list backlog tasks", "edit a task", "manage backlog", "open the backlog UI", or "switch backlog projects". Manages tasks, plans, and project state with backlog.md (a CLI + MCP task management tool), preferring the CLI over MCP when both are available.
---

# backlog

Manage tasks, plans, and project state in a repo where **backlog.md** is the
task management system.

`--help` is the source of truth for flags. Run `backlog <command> --help`
(e.g. `backlog task create --help`, `backlog task edit --help`) for the exact,
current options. This skill covers the rules and judgment `--help` can't.

## The golden rule

**The CLI is the only way to write.** Read with `backlog task <id> --plain`;
change only with `backlog task edit ...`. Editing the `.md` files in
`backlog/tasks/` directly breaks metadata sync, Git tracking, and task
relationships. If a change won't take, you're editing files — use the CLI.

Prefer the CLI over the backlog MCP server when both exist; use MCP only where
there's no shell. Always pass `--plain` when listing/viewing — clean,
AI-readable output.

## When to invoke

"manage backlog tasks", "list/create/edit a backlog task", "open the backlog
UI", "switch backlog projects".

## Workflow

```bash
backlog task list -s "To Do" --plain          # 1. find work
backlog task 42 --plain                        # 2. read details (+ any refs/docs)
backlog task edit 42 -s "In Progress" -a @you  # 3. claim it — FIRST thing you do
                                               #    (-a stores the literal string; use your own handle)
backlog task edit 42 --plan "1. Analyze        # 4. add plan (real newlines, not \n)
2. Build
3. Test"
# 5. share plan with the user, wait for approval before coding
backlog task edit 42 --check-ac 1 --check-ac 2 # 6. implement; check AC as you go
backlog task edit 42 --final-summary "..."     # 7. PR-style summary
backlog task edit 42 -s Done                   # 8. done
```

**Phase discipline:**

- _Creation_ — Title, Description, Acceptance Criteria, labels/priority/assignee.
  **No Implementation Plan at creation.**
- _Implementation_ — claim it (status + assignee) first, _then_ add the Plan;
  append Notes (`--append-notes`) as you go.
- _Wrap-up_ — Final Summary, then verify every AC and DoD is checked.

A task is **Done** only when all AC checked, all DoD checked, Final Summary
added, status `Done`, _and_ tests/docs/review pass.

## Gotchas `--help` won't tell you

- **Index flags repeat, not list.** `--check-ac 1 --check-ac 2` ✓ —
  `--check-ac 1,2` ✗, `--check-ac 1-2` ✗. Same for `--uncheck-*`, `--remove-*`.
- **`\n` stays literal.** The CLI stores input verbatim; `\n` inside quotes does
  _not_ become a newline. For multi-line notes/plans, repeat `--append-notes`
  per line, or put real newlines inside the quotes. Avoid `$'...'`,
  `$(printf ...)`, and heredocs — agent harnesses (Claude Code, Codex) reject
  them ([#595](https://github.com/MrLesk/Backlog.md/issues/595)).
- **Good ACs are outcomes, not steps.** "User can log in with valid
  credentials" ✓; "Add handleLogin() in auth.ts" ✗. Implement only what's in
  the AC — to do more, `--ac "New requirement"` first or create a follow-up task.
- **Final Summary is a PR description** — outcome first, then key changes, why,
  user impact, tests, risks. Not a one-liner unless truly trivial.
- **Task images** live under `backlog/assets/`; reference as
  `![alt](assets/images/foo.png)` (path starts with `assets/`, not the backlog
  dir name). Served by `backlog server`.

## Task file format (read-only)

What you'll _see_ in `backlog/tasks/task-<id> - <title>.md`. **Never edit it by
hand** — every field has a CLI flag (`backlog task edit --help`).

```markdown
---
id: task-42
title: Add GraphQL resolver
status: To Do
assignee: [@sara]
labels: [backend, api]
---

## Description

Brief explanation of the task purpose.

## Acceptance Criteria

<!-- AC:BEGIN -->

- [ ] #1 First criterion
- [x] #2 Second criterion (completed)
<!-- AC:END -->

## Definition of Done

<!-- DOD:BEGIN -->

- [ ] #1 Tests pass
<!-- DOD:END -->
```

The `AC:BEGIN`/`DOD:BEGIN` markers are CLI-managed — `--check-ac`/`--check-dod`
index flags operate on the numbered items inside them. Don't touch the markers
or renumber by hand. (Plan, Notes, and Final Summary follow as their own
sections, all set via `backlog task edit`.)

## Projects

Each project is one slot in a configured **global store** (`globalStore` in
`~/.config/backlog/config.yml`; override the config dir with
`BACKLOG_MACHINE_CONFIG_DIR`). Slots live at `<globalStore>/<name>/` (flat
`config.yml` + `tasks/`), keyed by name — **not** tied to repos. The machine
config's `current` pointer is what the long-running server / MCP fall back to;
switch via the CLI, don't hand-edit it.

- Create: `backlog project create <name>`
- List: `backlog project list` (`--plain` → `{"current", "projects":[{"id","name"}]}`)
- Switch: `backlog project switch <name>`
- One-off override: `backlog <cmd> --project <name>`
- Delete (soft, recoverable): `backlog project delete <name>`

Most commands act on the current project — run `backlog task list --plain` to
confirm you're targeting the right one.

## Server & remote

- `backlog server` — web UI in foreground (`http://localhost:3000`, `--open`
  launches the browser). `backlog browser` is a deprecated alias.
- `backlog service start|stop|status|logs|uninstall` — **macOS-only** launchd
  daemon so the UI outlives the terminal. Linux/Windows: use `backlog server`.
- A running server can create a project with no shell:
  `POST /api/projects {"name": "..."}`.
- **Remote server:** set `backlog_url` (+ optional `client_token`) in
  `~/.config/backlog/config.yml`. CLI, MCP, and web UI send `client_token` (or
  `BACKLOG_TOKEN`, which overrides it) as a bearer token; the server accepts any
  token in its `server_tokens` list. `BACKLOG_URL`/`BACKLOG_TOKEN` env vars
  override config.
- **Wire up an AI client:** `backlog mcp install <claude|codex|gemini|kiro>`
  configures the client to talk to the Backlog MCP server.

## Cloud / remote backend resolution

Confirm the backend is `backlog` from the repo's `spec/project.yaml`, and resolve
the project id from the `backlog_project` key (default `orchestrator` if unset):

```bash
grep "^ticketing:" spec/project.yaml | awk '{print $2}'        # -> backlog
grep "^backlog_project:" spec/project.yaml | awk '{print $2}'  # -> orchestrator (default)
```

Use the `backlog` **CLI only** for all task operations, scoped to that project — no
MCP or REST fallback. Reads via `backlog task view <ID> --plain`; status transitions
via `backlog task edit <ID> -s "<lane>"`. Resolve the real status-lane name first
(`backlog config get statuses`) so you never transition to a lane that doesn't exist.

```bash
backlog task view <ID> --plain            # read a task (scoped to the project)
backlog task edit <ID> -s "In Progress"   # transition status
```
