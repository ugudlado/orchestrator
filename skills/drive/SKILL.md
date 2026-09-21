---
name: drive
description: Drive a workflow to completion via `orchestrator next`. Use when running a ticket, feature, patch or bugfix workflow from a pack.
---

# Drive a workflow

The engine is a pure function: it says what runs next and stores nothing.
**You own all state** — history, attempts, approvals, the worktree.

**Inputs:** workflow name, pack path, slug, ticket id or brief text, repo
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
user_input: | # load-ticket-context: brief text or a ticket id
  <ticket text>
ticket_id: "" # set = ticket steps sync; "" = they no-op
workflow_plan: { main: { nodes: [] } } # check-rerun needs the key present
tokens: [] # yours: gate approvals
step_history: [] # yours: [{step, status, attempt, route, tier, model}]
```

Ticketing is off unless `BACKLOG_URL`+`BACKLOG_TOKEN` are set: ticket steps
no-op and `user_input` becomes `ticket-context.md` — the offline path.

## The loop

Call `$ORCH next <workflow> --config <pack> --slug <slug>` (no `--after` the
first time), act on `kind`, report, repeat until `status: done`.
`status: ready` is the normal answer: run `step_id`, then report it.
**Run the CLI with cwd = the run's working tree** (repo root until
`create-worktree`, the worktree after): artifact paths are relative and
`--out` checks resolve against cwd.

### kind: exec

Run `payload.run_path` with env = your env + `payload.env` + what the engine
cannot know. **`REPO_ROOT` is always the ORIGINAL repo, never the worktree** —
`create-worktree` needs it to create one. The worktree is named by its own
vars, and every artifact for a run must land there:

| var                                   | before create-worktree | after                     |
| ------------------------------------- | ---------------------- | ------------------------- |
| `REPO_ROOT`, `ORCHESTRATOR_REPO_ROOT` | repo                   | **repo** (unchanged)      |
| `ORCHESTRATOR_WORKFLOW_DIR`           | repo                   | worktree                  |
| `WORKTREE_PATH`, `WORKTREE_ROOT`      | unset                  | worktree                  |
| `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR`  | `<repo>/spec/changes`  | `<worktree>/spec/changes` |
| cwd for the CLI and scripts           | repo                   | worktree                  |

Also set: `STATE_YAML_PATH` + `ORCHESTRATOR_STATE_YAML_PATH` (absolute),
`ORCHESTRATOR_ATTEMPT` (your count — see Attempts), `ORCHESTRATOR_PYTHON` (an
interpreter that can import pyyaml), `BRANCH` and `ARCHIVE_PATH` once
`state_patch` reveals them, `WORKTREE_BASE_DIR` for where worktrees go.
Capture stdout to a file, then
`$ORCH next … --after <step> --exit-code <N> --stdout-file <file>`.
**Do not validate exec stdout yourself** — the engine parses it. Merge
`recorded.state_patch` into state.yaml before the next step.

### kind: judgment

Read `payload.prompt_path` (skip its `---` frontmatter; if it has `extends:`,
read the base charter first, and `learnings.md` in `step_dir` if present).
Read `payload.in`, **write every `payload.out` path** (mandatory whatever the
charter says — a missing one is rejected), produce a value per `out_schema`
key, then `--status completed --out '{"<key>": "<value>", …}'`.

**Substitute placeholders before briefing the worker**: charters contain
literal `{in.<name>}` / `{out.<name>}` (names `[A-Za-z0-9_-]+`) — replace from
`payload.in` / `payload.out`, leaving any name the payload lacks verbatim.
These are the only placeholders charters use. A charter may say "read the
installed `<role>` skill first"; if your harness has none, **the step's own
SKILL.md is sufficient.**

**Verdicts.** Either `--status failed` (as some charters instruct) or
`--status completed` works for a rejecting verdict: the engine derives failure
from `fail_on` either way. **Always put the verdict itself in `--out`** — that
is what the engine reads.

**Precedence:** where a charter and this skill disagree about CLI protocol
(status, attempts, paths), **this skill wins** — charters were written for an
older engine. Charters win on how to do the work.

Charters may ask the worker for `tokens_in`/`tokens_out`/`duration_s`: a
worker cannot measure those — fill them yourself, or omit/zero them.

### kind: gate

Show the `show` files and **wait for explicit user approval**; never
self-approve. On approval append `payload.approve_as` to `tokens`, then
`--after <gate> --status completed`. **Refuse any step whose
`payload.requires` token is not in `tokens`** — the engine cannot check it.

## Spawning: one fresh worker per step

`exec` → subprocess (no model). `judgment` → a fresh subagent with its own
context; failing that, a subprocess of your agent CLI with a model flag.
Inline only as a last resort. **Never run a review step in the same context
that produced the work.** Hand the worker: the charter (placeholders
substituted, plus its `extends` base and `learnings.md`), the `in` paths, the
`out` paths it must write, the `out_schema` keys to return as JSON, and cwd.

## Picking the model: choose a TIER, the pack maps tier → model

Read the charter's `description` plus payload signals. First match wins:

1. a `step_models` pin in `<pack>/models.yaml` → that tier
2. `attempt` > 1 or `route` == `on_failure` → one tier above that step's last
   attempt (order: fast < standard < code < strong)
3. designs / decides trade-offs / breaks down work, or its verdict gates other
   steps (`design`, `*-review`) → **strong** (review steps run tests but do
   not write code — rule 3, not 4)
4. writes code (`side_effects` has `write:git`, or `tools` has `git.commit` /
   `shell.test`) → **code**
5. read-only survey, summarize, reflect → **standard**
6. mechanical: format, status update, small `max_turns` → **fast**

Resolve the tier through `models:` in `<pack>/models.yaml`; use your nearest
equivalent if the harness lacks that model. Record `{step, tier, model}`.

## Outcomes

- `needs_you` → stop, give the user `reason`, ask.
- `await_input` → ask `await_input.ask`, re-run that step with the answer.
- `error` (`invalid out: …`) → re-run the step once with the error text; if it
  fails again, stop and ask.
- `done` → short report from `step_history`.

**persist-learnings**: it looks for `proposed-scenarios.jsonl` beside
`STATE_YAML_PATH` or in `ORCHESTRATOR_WORKFLOW_DIR` — but `learn`'s
`payload.out.proposed_scenarios` points into the artifacts dir. Write the
file where `payload.out` says, then **copy it next to state.yaml** before
running persist-learnings (pack bug: the contract declares the wrong place).
The script **deletes the staging file** when it finishes, so re-create it
before any retry.

## Attempts — count them yourself, or the run never stops

`--attempt N` for step X = **the number of `step_history` entries for X,
including the one you are about to report**. Per-step, for the whole run,
**never reset by a forward move**. The answer carries no `attempt` — the
engine has no history and cannot count for you.

Worked example (`code-review` has `on_failure: implement`, `max_retries: 8`):

| run                       | report                                                 | `--attempt`   |
| ------------------------- | ------------------------------------------------------ | ------------- |
| code-review rejects       | `--after code-review --out '{"verdict":"needs_work"}'` | 1             |
| implement fixes           | `--after implement --status completed`                 | 1             |
| code-review rejects again | `--after code-review …`                                | **2** — not 1 |

Passing 1 the second time is the bug that matters: with a reviewer that keeps
rejecting, the cap is never reached and the run loops forever. The cap is
checked against the **failing step's own** counter (the one in `--after`),
not its `on_failure` target's.

**Append to `step_history` once per step run, right after `next --after`
returns**, recording the status the ENGINE derived (`recorded.status`) — not
what you passed. A `completed` you sent can come back `failed` via `fail_on`,
and the entry has to say `failed` or your next count is wrong.
To resume, read the last entry and call `next --after` it. Log what you like.
