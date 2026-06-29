---
name: backlog-md
description: |
  Manage tasks, plans, and project state with backlog.md (a CLI + MCP task
  management tool). Use when the user asks to create, list, or edit tasks,
  run the backlog web UI, or work with multiple backlog projects.
---

# backlog-md

You are working in a repo (or environment) where backlog.md may be installed
as the task management system.

## Quick rules

- The CLI is the interface. Never edit task `.md` files directly — always go
  through `backlog task ...` commands.
- Projects live in a configured **global store** (`globalStore` in
  `~/.config/backlog/config.yml`), one slot per project keyed by name. Create
  with `backlog init <name>` or `backlog project create <name>`.
- To query a remote Backlog server instead of local files, set `backlog_url`
  (and optional `client_token`) in `~/.config/backlog/config.yml`. CLI and MCP
  share the same resolution — env vars `BACKLOG_URL` / `BACKLOG_TOKEN` override
  config when set.
- For the full command reference, read reference.md in this skill directory.
- Select the project to act on: `backlog project list`, `backlog project switch
  <name>`, or `--project <name>` per command. Without it, commands use the
  current project.
- The web UI runs in the foreground via `backlog server`, or as a background
  service: `backlog service start` on macOS (launchd), or the systemd recipe on
  Linux. See SERVICE.md in the backlog.md repo for the full Linux/Windows setup
  and the `scripts/service-{linux,macos}.sh` helpers.
- A running server can create a project without shell access:
  `POST /api/projects {"name": "My Project"}`.

## When to invoke

Trigger on user phrases like "manage backlog tasks", "list backlog tasks",
"create a backlog task", "open the backlog UI", or "switch backlog projects".

## Discovery

Run `backlog --help` for the full CLI surface, or read reference.md in this
skill directory for the canonical workflow guide.
