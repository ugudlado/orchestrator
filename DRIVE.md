# DRIVE.md — running the orchestrator as a Claude Code cloud session

You (the Claude Code session, e.g. invoked via `@Claude` in Slack) are the **driver**
for an orchestrator workflow. The orchestrator engine computes the next step; **you
execute it with your own model.** No subprocess is spawned, no per-step model routing —
you are the agent for every step.

This is the cloud/Slack path. The local path (`orchestrator run`) self-drives by
spawning per-step model subprocesses; ignore that here.

---

## v1 boundaries — know these before you start

- **Single session.** The run proceeds until it completes (`exit 1`) or hits a blocking
  signoff (`exit 2`). At a block you report and stop. Resuming after a block requires the
  state file to survive — see "Durability" below.
- **No worktree.** `create-worktree` detects `CLAUDE_CODE_REMOTE=true` and no-ops — you're
  already in an isolated sandbox on your own branch, so every step runs directly against
  the repo checkout instead of a local `~/code/feature_worktrees/...` dir.
- **Ticketing is via MCP**, not the engine. The engine's `ticket-*` script steps target
  the `backlog` CLI and **no-op cleanly** when the backend isn't `backlog` or the CLI is
  absent (they still return `completed`). You own ticket transitions through MCP tools.
- **Cost metrics will read $0** unless you report real token usage in each `done` payload
  (see step 4). This is expected; don't try to fix it mid-run.

---

## 0. Bootstrap

Install and `ORCHESTRATOR_SKIP_USAGE_CHECK` are handled for you before this runs:
the SessionStart hook (`.claude/cloud-setup.sh`) installs the package, and the env
var is set in the cloud environment (see `docs/cloud-environment.md`). You don't
run install steps here.

The install gives you the `orchestrator` console script (fallback if PATH is
stale: `python -m orchestrator_next`):

```bash
orchestrator <verb> ...
```

Config resolution is explicit — no cwd fallback. Export the config root once at the
start of the session, before any orchestrator verb:

```bash
export ORCHESTRATOR_CONFIG="$PWD/config"        # this repo's config
# or, for a wheel-only install without a checkout:
export ORCHESTRATOR_CONFIG=$(orchestrator config-path)   # bundled config
```

## 1. Read the ticket (MCP)

Detect the backend from `spec/project.yaml`:

```bash
grep "^ticketing:" spec/project.yaml | awk '{print $2}'   # → backlog | linear
```

- `linear` → use the Linear MCP tools. IDs are in `spec/project.yaml` under `linear:`.
- `backlog` → use the backlog MCP tools. **First read the server's own guides** so you
  use the right operations and statuses (status lane names vary per project):
  - resource `backlog://workflow/overview` — when/how to use the tools
  - the Task Execution and Task Finalization guides
  - tools are named `backlog.<operation>` (e.g. `backlog.list_tasks`, `backlog.get_task`,
    `backlog.edit_task`). List the tools to see exact names + arguments.

  Do **not** assume CLI command shapes (`backlog task edit ...`) — that path needs the
  local binary and your remote backend, which the cloud session may not have. Drive
  ticketing through the MCP tools, which proxy to the backend for you.

Read the ticket named in the Slack request. Use its body as context for the run.
Transition it to **In Progress** via the MCP tool (resolve the real status name from the
server first — don't hardcode "In Progress" if the project uses a different lane).

## 2. Seed the workflow

Pick the schema from the request (`feature`, `bugfix`, `chore`, `patch`, …).

```bash
STATE=$(orchestrator run <slug> --schema <schema> --seed-only | tail -1)
```

`--seed-only` creates `state.yaml` under `.orchestrator/<slug>/` and stops — it does
**not** drive the workflow (that's your job, via the loop below). The path is printed on
the last line. It's idempotent: re-running resolves the existing state instead of seeding
a duplicate.

## 3. The drive loop

Repeat until the engine says stop:

```bash
orchestrator next "$STATE"
```

Interpret by exit code:

| Exit                      | Meaning                                                                               | What you do                                               |
| ------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `1`                       | workflow complete                                                                     | break — go to step 5                                      |
| `2`                       | blocked (signoff)                                                                     | report the reason to the Slack thread, then **stop** (v1) |
| `3`                       | error                                                                                 | report the error, stop                                    |
| `0` + JSON with `"run"`   | a **script step** — it already executed inside `next`                                 | loop again, do nothing                                    |
| `0` + JSON with `"model"` | an **agent step** — JSON has `instruction`, `step_id`, `phase`, `env`, `step_context` | execute it yourself (below), then `done`                  |

For an agent step:

1. Read `instruction` — this is your task. Read files, edit code, run tests **as it
   says.** Honor any "Verify" section: do not mark complete until verification passes.
   (Project rule: evidence-based — never claim completion without verification.)
2. Record the result (step 4).

## 4. Record each agent step

After completing an agent step's instruction, pipe a JSON payload to `done`:

```bash
echo '{
  "step_id": "<from the next JSON>",
  "phase":   "<from the next JSON>",
  "status":  "completed",
  "agent":   "<your model id, e.g. claude-opus-4-8>",
  "usage":   {"input_tokens": <real if known else 0>, "output_tokens": <real if known else 0>},
  "outputs": { ... any outputs the step contract requires ... }
}' | orchestrator done "$STATE"
```

- `agent` is **required** for agent steps.
- `usage`: report the real token counts your harness shows if you can — that keeps cost
  metrics correct. If you can't, `0`/`0` is accepted because `ORCHESTRATOR_SKIP_USAGE_CHECK`
  is set (cost will record as $0 for that step).
- If `done` returns a validation error (exit 3), read the `hint` — usually a missing
  required `output`. Add it and re-run `done`.
- Then loop back to step 3 (`next`).

## 5. Finish

- Move the ticket to its terminal status via MCP (**In Review** or **Done** per your
  workflow's convention).
- Summarize what was done in the Slack thread.
- Offer / create a PR (the Slack integration's "Create PR" button, or open one directly).

---

## Durability (resume after a block)

`state.yaml` lives in the repo working tree (`.orchestrator/<slug>/`). The cloud session's
filesystem is **ephemeral** — it's gone when the session ends.

You get durability for free: because `CLAUDE_CODE_REMOTE=true` marks the session headless,
every `orchestrator done` auto-commits the state dir (`git add -f` — it's gitignored), and
when a step transitions the run to **blocked** it also pushes (`git push origin HEAD`).

The one case you still handle manually: ending a session mid-run **without** a block —
push the branch yourself so the auto-commits survive:

```bash
git push origin HEAD
```

A future session resumes by pulling the branch and re-running `next "$STATE"`. Without the
state on the remote, a blocked workflow **cannot be resumed** — it must be re-seeded.

---

## What you are NOT doing

- Not spawning `claude`/`pi`/`cursor` subprocesses — **you** are the model for every step.
- Not using `models.yaml` per-step routing — one session, one model, by design.
- Not relying on the engine for ticket transitions — that's you, via MCP.
