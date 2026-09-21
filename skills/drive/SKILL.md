---
name: drive
description: Drive a workflow to completion via `orchestrator next`. Use when running a ticket, feature, patch or bugfix workflow from a pack.
---

# Drive a workflow

The engine is a pure function: it says what runs next and stores nothing.
**You own all state** — history, attempts, approvals, the worktree.

**Inputs:** workflow name, pack path, slug, the ticket id or brief text, repo
root. `$ORCH` is how this machine invokes the CLI (`orchestrator` or
`python -m orchestrator_next`).

## Before you start

Create `<repo>/.orchestrator/<slug>/state.yaml`; the pack's scripts read it:

```yaml
change_id: <slug> # check-rerun, load-ticket-context
slug: <slug>
schema: <workflow> # create-worktree (branch name)
status: active
repo_root: <abs repo path> # check-rerun
user_input: | # load-ticket-context: the brief, or a bare ticket id
  <ticket text>
ticket_id: "" # set it and ticket steps sync; '' = they no-op
workflow_plan: { main: { nodes: [] } } # check-rerun requires the key to exist
tokens: [] # yours: gate approvals
step_history: [] # yours: [{step, status, attempt, route}]
```

Ticketing is off unless `BACKLOG_URL`+`BACKLOG_TOKEN` are set: ticket steps
no-op and `user_input` becomes `ticket-context.md`. That is the offline path.

## The loop

Call `$ORCH next <workflow> --config <pack> --slug <slug>` (no `--after` the
first time), act on `kind`, report, repeat until `status: done`.

**Run the CLI with cwd = the run's working tree** — the repo root until
`create-worktree` reports `worktree_path`, the worktree after. Artifact paths
are relative and `--out` checks resolve against cwd.

### kind: exec

Run `payload.run_path` with env = your env + `payload.env` + what the engine
cannot know: `REPO_ROOT`, `ORCHESTRATOR_REPO_ROOT` (= cwd), `STATE_YAML_PATH`,
`ORCHESTRATOR_STATE_YAML_PATH`, plus `BRANCH`, `WORKTREE_PATH`,
`WORKTREE_ROOT`, `ARCHIVE_PATH` once `state_patch` reveals them
(`WORKTREE_BASE_DIR` sets where worktrees go). Capture stdout to a file, then
`$ORCH next … --after <step> --exit-code <N> --stdout-file <file>`.
Merge `recorded.state_patch` into state.yaml before the next step.

### kind: judgment

Read `payload.prompt_path` (skip its `---` frontmatter); if that frontmatter
has `extends:`, read the base charter first, and read `learnings.md` in
`step_dir` if present. Do the work yourself, or spawn a subagent with that
charter if your harness has them. Read `payload.in`, write every `payload.out`
path, produce a value per `out_schema` key, then
`--status completed --out '{"<key>": "<value>", …}'`.
**Use `--status failed`** when the step's verdict rejects the work — an enum
the contract marks as failing (`needs_work`, `incomplete_phase`, `rework`).
Still pass the verdict in `--out`. That rejection is what sends the flow back.

### kind: gate

Show the `show` files and **wait for explicit user approval**; never
self-approve. On approval append `payload.approve_as` to `tokens`, then
`--after <gate> --status completed`. **Refuse any step whose
`payload.requires` token is not in `tokens`** — the engine cannot check it.

## Outcomes

- `needs_you` → stop, give the user `reason`, ask.
- `await_input` → ask `await_input.ask`, re-run that step with the answer,
  report it again with `--after <same step>`.
- `error` (`invalid out: …`) → re-run the step once with the error text; if it
  fails again, stop and ask.
- `done` → short report from `step_history`.

`--attempt N` = 1 + prior `step_history` entries for that step; the engine
stops at `attempt >= max_retries`. To resume, read the last history entry and
call `next --after` it. Log whatever else you find useful.
