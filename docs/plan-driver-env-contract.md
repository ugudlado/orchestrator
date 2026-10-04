# Driver env contract

Outcome of the Sep 22–24 2026 telemetry experiment (core-only, two drive-loop
runs, OTEL → collector → Langfuse). Findings and the change they imply.

## What we learned

- Per-step metrics (model, tokens, cost, duration) are a **driver** concern:
  every agent CLI returns them in its JSON output (`claude -p --output-format
json`, `codex exec --json`, `gemini --output-format json`, `opencode run
--format json`). The driver writes them into `step_history`. No engine code.
- Observability backends (Langfuse etc.) are **machine config**, never engine
  or pack code. The engine emits identity; the machine decides what to do
  with it.
- Every piece of driver friction in the runs was the same bug: _the driver
  had to know a secret_ — an env var a pack script demands, a path a script
  writes somewhere the next step doesn't read, a charter naming a skill that
  doesn't exist in a subprocess. Fix the contract, not the driver.

## The contract

Spawn env for **every** step (exec and judgment) = caller env + `payload.env` + the driver block.

Engine (`next`, already emitted — **no change**):

| var                                                    | value          |
| ------------------------------------------------------ | -------------- |
| `ORCHESTRATOR_STEP_ID`                                 | step id        |
| `ORCHESTRATOR_CHANGE_ID`, `CHANGE_ID`                  | slug           |
| `ORCHESTRATOR_STEP_DIR`                                | step directory |
| `ORCHESTRATOR_PROMPT_DIRS`, `ORCHESTRATOR_PROMPT_PATH` | prompt roots   |

Driver block (the engine is stateless and cannot know these; the driver
already does):

| var                    | value                                             |
| ---------------------- | ------------------------------------------------- |
| `ORCHESTRATOR_ATTEMPT` | this step's attempt, same number as `--attempt`   |
| `REPO_ROOT`            | the repository the run started in                 |
| `WORKTREE_PATH`        | the run worktree once it exists, else `REPO_ROOT` |
| `STATE_YAML_PATH`      | the driver's state file                           |
| `TICKET_ID`            | the ticket, if any                                |

That is the whole list. Anything else a script needs comes through
`payload.in` / `payload.env` (contract `params:`), not from reading the
driver's state file.

`step_history` entry shape (driver, per run of a step):

```yaml
- step: implement
  attempt: 1
  status: completed # engine-derived (recorded.status)
  model: claude-sonnet-5
  started: 2026-09-24T11:15:02Z
  ended: 2026-09-24T11:16:57Z
  duration_ms: 114864
  usage: { input: 38, output: 5343, cache_read: 1249433, cache_write: 57486 }
  cost_usd: 0.533 # from the CLI when it reports one, else omitted
```

Exec steps record `step`, `attempt`, `status`, `started`, `ended` only.

## Changes

### Engine (`core-only`) — none

`payload.env` is already the engine's complete contribution. Do not add
attempt (the engine has no history) or paths (it has no state). Do not add
telemetry vars of any kind.

### Driver skill (now `orchestrate` in ugudlado/skills, `workflow/orchestrate/SKILL.md`)

1. Replace "env = your env + `payload.env` + whatever DRIVER.md says" with the
   driver-block table above; it applies to judgment spawns too.
2. Add the `step_history` entry shape and where the numbers come from
   (the worker CLI's JSON result).
3. Brief construction: **inline** the charter text (frontmatter stripped,
   `{in.*}`/`{out.*}` substituted, `extends` base prepended) — a subprocess
   worker cannot read absolute paths outside its cwd. Delete the "read the
   installed `<role>` skill" fallback language; it never applies to a
   subprocess.
4. `next --out` paths resolve against the CLI's cwd: run `next` from
   `WORKTREE_PATH`.
5. One line, no more: "Run tagging for external tools (e.g.
   `OTEL_RESOURCE_ATTRIBUTES`) is machine config built from these vars; the
   driver sets nothing for it."

### Pack (`ugudlado/workflows`) — separate repo, separate PR

1. Scripts that `${STATE_YAML_PATH:?}` / grep the state file
   (`lib/ticket/ticket-sync.sh`, `create-worktree`, `load-ticket-context`,
   `merge-to-main`, `remove-worktree`, `archive-completed-change`,
   `ticket-done`) read `TICKET_ID`, `WORKTREE_PATH`, `CHANGE_ID` from env
   instead. After this the "pack scripts read STATE_YAML_PATH" blocker on
   core-only is gone.
2. `load-ticket-context` writes under `WORKTREE_PATH`, not `REPO_ROOT` —
   the next step's `in.ticket` resolves against the worktree.
3. `create-worktree` keeps git's banner off stdout (`git worktree add
… >&2`), so `--stdout-file` is pure JSON.
4. Add `DRIVER.md` at the pack root: the driver-block table, verbatim.
5. Charters: drop "read the installed `developer` skill first"; the charter
   is the brief.

### Not doing

- Langfuse / OTEL anything in engine, skill, or pack.
- A `report` verb. `step_history` is the record; render it when someone
  needs a page.
- Engine passthrough of driver vars (`next --env`). The driver sets its own
  env.

## Verification

Drive `patch` on core-only with a subprocess worker and a driver that sets
**only** the five driver-block vars. Every exec script runs without
synthesizing anything; `implement` receives its ticket from the worktree
path; `step_history` carries usage/cost/duration for each judgment step.
