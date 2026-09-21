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

**If `<pack>/DRIVER.md` exists, read it first** — it lists what that pack's
scripts need (state file keys, environment, quirks). This skill is the
protocol; DRIVER.md is the pack. Keep a run state file (one per run, e.g.
`<repo>/.orchestrator/<slug>/state.yaml`) with at least `tokens: []` for gate
approvals and `step_history: []`.

## The loop

Call `$ORCH next <workflow> --config <pack> --slug <slug>` (no `--after` the
first time), act on `kind`, report, repeat until `status: done`.
`status: ready` is the normal answer: run `step_id`, then report it.
**Run the CLI with cwd = the run's working tree** (the repo root until a
worktree exists, the worktree after): artifact paths are relative and `--out`
checks resolve against cwd.

### kind: exec

Run `payload.run_path` with env = your env + `payload.env` + whatever DRIVER.md
says the pack needs. Capture stdout to
`<state dir>/results/<step>-<attempt>.stdout`, then
`$ORCH next … --after <step> --exit-code <N> --stdout-file <that file>`.
**Do not validate exec stdout yourself** — the engine parses it. Merge any
`recorded.state_patch` into your state file before the next step.

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
- rejected → `--after <gate> --status failed`. A gate with no `on_failure`
  answers `needs_you`; ask the user which step to go back to and report that
  rejection as `--out '{"reset_to":"<step>"}'`, which routes there directly.
  `--status abandoned` simply re-presents the same gate.

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
that produced the work.** Hand the worker: the charter (placeholders
substituted, plus its `extends` base and `learnings.md`), the `in` paths, the
`out` paths it must write, the `out_schema` keys to return as JSON, the result
file path it must write, and the cwd.

## Picking the model: choose a TIER, the pack maps tier → model

From the charter's `description` plus payload signals; first match wins:

1. a `step_models` pin in `<pack>/models.yaml` → that tier
2. `attempt` > 1 or `route` == `on_failure` → one tier above that step's last
   attempt (order: fast < standard < code < strong)
3. designs, decides trade-offs, breaks down work, or its verdict gates other
   steps → **strong** (reviews run tests but write no code — rule 3, not 4)
4. writes code (`write:git`, or `git.commit` / `shell.test` in tools) → **code**
5. read-only survey, summarize, reflect → **standard**
6. mechanical: format, status update, small `max_turns` → **fast**

Resolve via `models:` in `<pack>/models.yaml`; use your nearest equivalent if
the harness lacks that model. Record `{step, tier, model}`.

## Outcomes

- `needs_you` → stop and tell the user `reason`. On "retry", re-run that step
  and report it again with `--attempt` one higher. After `retries exhausted`
  the options are: fix the blocker and retry anyway, send the run back to an
  earlier step (`--status failed --out '{"reset_to":"<step>"}'`), or abandon.
- `await_input` (exec) → ask `await_input.ask`, re-run that step with the
  answer. A judgment reporting an `await_input`-ish value in `--out` is **not
  finished**: ask, re-run it, report the real decision.
- `error` (`invalid out: …`) → re-run the step once with the error text; if it
  fails again, stop and ask.
- `done` → short report from `step_history`.

## Attempts — count them yourself, or the run never stops

`--attempt N` for step X = **the number of `step_history` entries for X,
including the one you are about to report**. Per-step, for the whole run,
**never reset by a forward move**. The answer carries no `attempt` — the engine
has no history and cannot count for you.

Worked example (`code-review` has `on_failure: implement`, `max_retries: 8`):

| run                       | report                                                 | `--attempt`   |
| ------------------------- | ------------------------------------------------------ | ------------- |
| code-review rejects       | `--after code-review --out '{"verdict":"needs_work"}'` | 1             |
| implement fixes           | `--after implement --status completed`                 | 1             |
| code-review rejects again | `--after code-review …`                                | **2** — not 1 |

Passing 1 the second time is the bug that matters: with a reviewer that keeps
rejecting, the cap is never reached and the run loops forever. The cap is
checked against the **failing step's own** counter (the one in `--after`).

**Append to `step_history` once per step run, right after `next --after`
returns**, recording the status the ENGINE derived (`recorded.status`) — not
what you passed. To resume, read the last entry and call `next --after` it.

**Precedence:** where a charter and this skill disagree about CLI protocol
(status, attempts, paths), **this skill wins** — charters were written for an
older engine. Charters win on how to do the work.
