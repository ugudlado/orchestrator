---
name: drive
description: Drive a workflow to completion via `orchestrator next`. Use when running a ticket, feature, patch or bugfix workflow from a pack.
---

# Drive a workflow

The engine is a pure function: it says what runs next and stores nothing.
**You own all state** — history, attempts, approvals, the worktree.

**Inputs:** workflow name, pack path, slug, ticket id or brief text, repo root,
and `$ORCH` — how this machine invokes the CLI. `$ORCH` is usually several argv
tokens (`python -m orchestrator_next`): never quote it as one word — write
`$ORCH next …`, or build an argv list.

**If `<pack>/DRIVER.md` exists, read it too** — it may note pack-specific
quirks. This skill is the protocol. Keep a run state file (one per run, e.g.
`<repo>/.orchestrator/<slug>/state.yaml`) with at least `tokens: []` for gate
approvals and `step_history: []`.

## Spawn env: every step, exec or judgment

env = your env + `payload.env` + this driver block:

| var                    | value                                             |
| ---------------------- | ------------------------------------------------- |
| `ORCHESTRATOR_ATTEMPT` | this step's attempt, same number as `--attempt`   |
| `REPO_ROOT`            | the repository the run started in                 |
| `WORKTREE_PATH`        | the run worktree once it exists, else `REPO_ROOT` |
| `STATE_YAML_PATH`      | your state file                                   |
| `TICKET_ID`            | the ticket, if any                                |

That is the whole list — nothing else, no telemetry vars. Anything a script
needs beyond this comes through `payload.in` / `payload.env`
(contract `params:`), not by reading your state file.

## The loop

Call `$ORCH next <workflow> --config <pack> --slug <slug>` (no `--after` the
first time), act on `kind`, report, repeat until `status: done`.
`status: ready` is the normal answer: run `step_id`, then report it.
**Run the CLI with cwd = `WORKTREE_PATH`** (the repo root until a worktree
exists, the worktree after): `--out` paths and `payload.in` resolve against
the CLI's cwd, so `next` must be run from there.

### kind: exec

Run `payload.run_path` with the spawn env above. Capture stdout to
`<state dir>/results/<step>-<attempt>.stdout`, then
`$ORCH next … --after <step> --exit-code <N> --stdout-file <that file>`.
**Do not validate exec stdout yourself** — the engine parses it. Merge any
`recorded.state_patch` into your state file before the next step.
On a non-zero exit the engine **replaces** the script's outputs with
`{"reason": "script exited N"}`, so read the stdout file yourself if you need
what it said.

### kind: judgment

Read `payload.prompt_path` (skip its `---` frontmatter; if it has `extends:`,
read the base charter first, and `learnings.md` in `step_dir` if present).
**Inline this into the worker's brief** — a subprocess worker cannot read
absolute paths outside its own cwd, so the charter text itself has to travel
in the prompt, not a path to it.
Read `payload.in`, **write every `payload.out` path** (mandatory whatever the
charter says — a missing one is rejected), produce a value per `out_schema`
key, then `--status completed --out '{"<key>": "<value>", …}'`.
An out whose `out_schema` entry says `optional: true` **may be left out
entirely — never write a stub**: a stub is read by the next step as real work.
Naming a path in `--out` is a claim it exists, and the claim is checked even
when optional. A pack may also want an artifact somewhere its own scripts
read — DRIVER.md says where (this pack's `learn` staging file is one).

**Substitute placeholders before briefing the worker**: charters contain
literal `{in.<name>}` / `{out.<name>}` (names `[A-Za-z0-9_-]+`) — replace from
`payload.in` / `payload.out`, leaving any name the payload lacks verbatim.
These are the only placeholders charters use.

**Verdicts.** Either `--status failed` or `--status completed` works for a
rejecting verdict: the engine derives failure from `fail_on` either way.
**Always put the verdict itself in `--out`** — that is what the engine reads.

Charters may ask the worker for `tokens_in`/`tokens_out`/`duration_s`: a worker
cannot measure those — fill them yourself, or omit/zero them.

### kind: gate

Show the `show` files and **wait for explicit user approval**; never
self-approve. **Refuse any step whose `payload.requires` token is not in
`tokens`** — the engine cannot check it.

- approved → append `payload.approve_as` to `tokens`, then
  `--after <gate> --status completed`.
- approved with changes → record the note in your state file (the engine never
  interpreted gate edits), then approve as above and brief the next worker
  with it.
- rejected, user named a step → `--after <gate> --status failed --out
'{"reset_to":"<step>"}'` routes straight there, in one call.
- rejected, no step named → `--after <gate> --status failed`. A gate with no
  `on_failure` answers `needs_you`; ask which step to go back to, then make
  the call above. `--status abandoned` re-presents the same gate unchanged.

Gates take `--attempt` too, counted per presentation of that gate.

## Results: the handback is the file

**Every judgment worker writes its result JSON to
`<state dir>/results/<step>-<attempt>.json` BEFORE returning** — exactly the
object that will become `--out`, plus `status` and a short `summary` — and its
handback is that same JSON, nothing more authoritative.

**Read the file, not the handback**: handbacks get lost, and nested ones
reliably do. Verify the declared `out` artifacts exist, then call `next`.
**A missing file means the step did not finish** — re-run it at the same
attempt number rather than guessing what it did.

## Spawning: one fresh worker per step

`exec` → subprocess (no model). `judgment` → a fresh subagent with its own
context; failing that, a subprocess of your agent CLI with a model flag.
Inline only as a last resort. **Never run a review step in the same context
that produced the work.** Hand the worker: the charter, inlined into the
brief (frontmatter stripped, placeholders substituted, `extends` base
prepended, `learnings.md` appended if present), the `in` paths, the
`out` paths it must write, the `out_schema` keys to return as JSON, the result
file path it must write, the spawn env, and the cwd.

Run tagging for external tools (e.g. `OTEL_RESOURCE_ATTRIBUTES`) is machine
config built from these vars; the driver sets nothing for it.

## Picking the model: choose a TIER, the pack maps tier → model

From the charter's `description` plus payload signals; first match wins:

1. a `step_models` pin in `<pack>/models.yaml` → that tier
2. `attempt` > 1 or `route` == `on_failure` → one tier above that step's last
   attempt (order: fast < standard < code < strong)

**Rule 1 outranks rule 2**: a pinned step keeps its pinned tier on a retry —
the pack author chose it deliberately. Escalation applies to unpinned steps. 3. designs, decides trade-offs, breaks down work, or its verdict gates other
steps → **strong** (reviews run tests but write no code — rule 3, not 4) 4. writes code (`write:git`, or `git.commit` / `shell.test` in tools) → **code** 5. read-only survey, summarize, reflect → **standard** 6. mechanical: format, status update, small `max_turns` → **fast**

Resolve via `models:` in `<pack>/models.yaml`; use your nearest equivalent if
the harness lacks that model. Record `{step, tier, model}`.

## Outcomes

- `needs_you` **with `reason`** → stop and tell the user `reason`. On "retry", re-run that step
  and report it again with `--attempt` one higher. After `retries exhausted`
  the options are: fix the blocker and retry anyway, send the run back to an
  earlier step (`--status failed --out '{"reset_to":"<step>"}'`), or abandon.
- `needs_you` **with an `await_input` key** → the step asked. Show the user
  `await_input.ask` (and `options` if present) and wait. **Only the user
  answers** — never answer from conversation context, defaults or your own
  judgment, for any step (same rule as gates). Append a `step_history` entry when the ask is shown, with no `answer` yet
  (`{step, attempt, status: await_input, ask, started, ended}`; `status` =
  `recorded.status`; `attempt` = the number the re-run will report, i.e. 1 +
  the step's non-ask entries), and add `answer` once the user replies. Then
  rename the ask's result file (or `.stdout`) to
  `results/<step>-<attempt>-ask<k>.json` (k = the step's ask count), so
  a re-run that dies before writing cannot be mistaken for the ask. Re-run the
  **same** step with `User direction: <answer>` in the worker brief and report
  its real outcome.
- `error` (`invalid out: …`) → **nothing was recorded**: do not append to
  `step_history` and do not advance `--attempt`. Re-run the step once with the
  error text at the same attempt; if it fails again, stop and ask.
- `done` → short report from `step_history`.

## Attempts — count them yourself, or the run never stops

`--attempt N` for step X = **the number of `step_history` entries for X
whose status is not `await_input`, including the one you are about to
report**. Asks never count toward `max_retries`. Per-step, for the whole run,
**never reset by a forward move**. The answer carries no `attempt` — the engine
has no history and cannot count for you.

Worked example (`code-review` has `on_failure: implement`, `max_retries: 8`):

| run                                                                 | report                                                 | `--attempt`   |
| ------------------------------------------------------------------- | ------------------------------------------------------ | ------------- |
| code-review rejects                                                 | `--after code-review --out '{"verdict":"needs_work"}'` | 1             |
| implement fixes                                                     | `--after implement --status completed`                 | 1             |
| code-review rejects again                                           | `--after code-review …`                                | **2** — not 1 |
| human-review asks (entry `await_input`, attempt 1), re-run approves | `--after human-review …`                               | 1             |

Passing 1 the second time is the bug that matters: with a reviewer that keeps
rejecting, the cap is never reached and the run loops forever. The cap is
checked against the **failing step's own** counter (the one in `--after`).

**Append to `step_history` once per step run, right after `next --after`
returns**, recording the status the ENGINE derived (`recorded.status`) — not
what you passed. For a judgment step, the entry also carries `model`,
`started`, `ended`, `duration_ms`, `usage`, and `cost_usd` (omit if the CLI
didn't report one) — these come from the worker CLI's own JSON result, not
from anything the engine emits (e.g. an `--output-format json` flag on the
CLI you spawned):

```yaml
- step: implement
  attempt: 1
  status: completed # engine-derived (recorded.status)
  model: claude-sonnet-5
  started: 2026-09-24T11:15:02Z
  ended: 2026-09-24T11:16:57Z
  duration_ms: 114864
  usage: { input: 38, output: 5343, cache_read: 1249433, cache_write: 57486 }
  cost_usd: 0.533
```

Exec entries record `step`, `attempt`, `status`, `started`, `ended` only —
no model/usage/cost.

To resume, read the last entry. If its status is `await_input` without
`answer`, ask the user again; with `answer`, re-run that step with it as User
direction. Do not replay `next --after` for an ask entry. Otherwise call
`next --after` it:
that call is a **replay, not a run** — it returns the step you never got to,
so do not append an entry for it. Append only for steps you actually ran.

**Precedence:** where a charter and this skill disagree about CLI protocol
(status, attempts, paths), **this skill wins** — charters were written for an
older engine. Charters win on how to do the work.
