---
name: drive
description: Drive a workflow to completion via `orchestrator next`. Use when running a ticket, feature, patch or bugfix workflow from a pack.
---

# Drive a workflow

The engine is a pure function: it says what runs next and stores nothing.
**You own all state** — history, attempts, approvals, the worktree.

**Inputs:** workflow name, pack path, slug, the ticket id or brief text, repo
root, and `$ORCH` — how this machine invokes the CLI. `$ORCH` is usually
several argv tokens (`python -m orchestrator_next`): never quote it as one
word — write `$ORCH next …`, or build an argv list.

## Before you start

Create ONE state.yaml at `<repo>/.orchestrator/<slug>/state.yaml`; pass its
**absolute** path as `STATE_YAML_PATH`. It stays there all run — never copied
into the worktree. The pack's scripts read:

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
step_history: [] # yours: [{step, status, attempt, route, tier, model}]
```

Ticketing is off unless `BACKLOG_URL`+`BACKLOG_TOKEN` are set: ticket steps
no-op and `user_input` becomes `ticket-context.md`. That is the offline path.

## The loop

Call `$ORCH next <workflow> --config <pack> --slug <slug>` (no `--after` the
first time), act on `kind`, report, repeat until `status: done`.
`status: ready` is the normal answer: run `step_id`, then report it.
**Run the CLI with cwd = the run's working tree** — the repo root until
`create-worktree` reports `worktree_path`, the worktree after. Artifact paths
are relative and `--out` checks resolve against cwd.

### kind: exec

Run `payload.run_path` with env = your env + `payload.env` + what the engine
cannot know: `REPO_ROOT`, `ORCHESTRATOR_REPO_ROOT` (= cwd), `STATE_YAML_PATH`,
`ORCHESTRATOR_STATE_YAML_PATH` (absolute), `ORCHESTRATOR_PYTHON` (an
interpreter that can import the pack's deps, e.g. pyyaml), plus `BRANCH`,
`WORKTREE_PATH`, `WORKTREE_ROOT`, `ARCHIVE_PATH` once `state_patch` reveals
them. `WORKTREE_BASE_DIR` sets where worktrees go.
Capture stdout to a file, then
`$ORCH next … --after <step> --exit-code <N> --stdout-file <file>`.
**Do not validate exec stdout yourself** — the engine parses it. Merge
`recorded.state_patch` into state.yaml before the next step.

### kind: judgment

Read `payload.prompt_path` (skip its `---` frontmatter); if that frontmatter
has `extends:`, read the base charter first, and read `learnings.md` in
`step_dir` if present. Read `payload.in`, **write every `payload.out` path**
(they are mandatory whatever the charter says — a missing one is rejected),
and produce a value per `out_schema` key. Then
`--status completed --out '{"<key>": "<value>", …}'`.

**Substitute placeholders before briefing the worker.** Charters contain
literal `{in.<name>}` and `{out.<name>}` — replace each with the matching path
from `payload.in` / `payload.out` (names are `[A-Za-z0-9_-]+`). Leave any name
the payload does not define exactly as written rather than blanking it. These
are the only placeholders charters use.

A charter may say "read the installed `<role>` skill first" (developer,
code-reviewer, learner…). If your harness has no such skill, **the step's own
SKILL.md is sufficient — proceed with it.**

**Verdicts.** Either `--status failed` (as some charters instruct) or
`--status completed` works for a rejecting verdict: the engine derives failure
from `fail_on` either way. **Always put the verdict itself in `--out`** — that
is what the engine reads. Ignore charter warnings that `completed` disarms the
routing; that was true of an older engine, not this one.

A charter may ask the worker for `tokens_in` / `tokens_out` / `duration_s`.
A worker cannot measure those: fill them yourself if your harness exposes them,
otherwise omit them or send zeros.

### kind: gate

Show the `show` files and **wait for explicit user approval**; never
self-approve. On approval append `payload.approve_as` to `tokens`, then
`--after <gate> --status completed`. **Refuse any step whose
`payload.requires` token is not in `tokens`** — the engine cannot check it.

## Spawning: one fresh worker per step

`exec` → subprocess (no model). `judgment` → a fresh subagent with its own
context; if your harness has no subagents, run a subprocess of your agent CLI
with a model flag. Inline only as a last resort. **Never run a review step in
the same context that produced the work.** Hand the worker: the charter
(placeholders already substituted, plus its `extends` base and `learnings.md`),
the `in` paths, the `out` paths it must write, the `out_schema` keys to return
as JSON, and the cwd.

## Picking the model: choose a TIER, the pack maps tier → model

Read the charter's `description` plus payload signals. First match wins:

1. a `step_models` pin in `<pack>/models.yaml` → that tier
2. `attempt` > 1 or `route` == `on_failure` → one tier above that step's last
   attempt (order: fast < standard < code < strong)
3. designs / decides trade-offs / breaks down work, or its verdict gates other
   steps (`design`, `*-review`) → **strong**
4. writes code (`side_effects` has `write:git`, or `tools` has `git.commit` /
   `shell.test`) → **code**
5. read-only survey, summarize, reflect → **standard**
6. mechanical: format, status update, small `max_turns` → **fast**

Resolve the tier through `models:` in `<pack>/models.yaml`; if your harness
lacks that model use its nearest equivalent. Record `{step, tier, model}` in
`step_history`.

## Outcomes

- `needs_you` → stop, give the user `reason`, ask.
- `await_input` → ask `await_input.ask`, re-run that step with the answer.
- `error` (`invalid out: …`) → re-run the step once with the error text; if it
  fails again, stop and ask.
- `done` → short report from `step_history`.

## Attempts

`--attempt N` is **how many times the step you are reporting has now been
run, counting this one**: 1 the first time, 2 after one failure round-trip
back to it. The engine stops with `retries exhausted` at `N >= max_retries`,
so a miscount either loops forever or gives up early. Count from
`step_history`. To resume, read the last entry and call `next --after` it.
Log whatever else you find useful.
