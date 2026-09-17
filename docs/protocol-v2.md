# Orchestrator Protocol v2

**Status:** normative spec for the simplification effort
(`orchestrator-simplification-plan.md`). Every phase after Phase 0 implements
against this doc. Where the plan is silent, this doc says **TBD (Phase N)**
rather than inventing behavior.

This supersedes `docs/pack-convention.md` protocol v1 for the parts that
change (contract keys, CLI verbs). v1's layout (`pack.yaml`, `steps/<id>/`)
and aliasing model stay in effect — see "Migration from v1" below.

---

## 1. Principles

1. **Harness executes, CLI enforces.** The engine never spawns vendor CLIs.
   It computes the next step, validates inputs/outputs, and records. The
   harness (Claude Mod, Codex plugin, or the CLI's own headless mode) runs
   the model.
2. **Steps are skills with contracts.** Each step dir = `contract.yaml` +
   `SKILL.md` (judgment) or `script.*` (exec) + `scenarios/`. Contracts are
   typed: `in`, `out`, `tools`, `side_effects` (model alias lives in
   `models.yaml step_models:`, not the contract).
3. **Judgment steps run as isolated subagents.** Fresh context per step:
   only that step's `SKILL.md`, resolved inputs, allowed tools, output
   schema.
4. **Artifacts are named, not pathed.** Steps reference `{in.x}` /
   `{out.y}`; the engine owns the run base path and resolves names to
   paths.
5. **Writes need a gate token.** Signoff is a resumable `gate` step, not a
   blocking exit code.
6. **Run data lives in a DB, not git.** SQLite default, Postgres via
   `ORCHESTRATOR_STATE_URL`. Packs stay in git; runs, usage, events, learn
   results do not.
7. **Aliases, not vendors.** Contracts say `strong|standard|fast|code`; the
   harness (or `~/.orchestrator/models.yaml` in headless mode) maps to real
   models and fallback chains. Contracts never carry a `model:` key.

---

## 2. Target architecture

```
Claude Code process
 ├─ Mod (TypeScript): tool.register("orchestrator.run"), $.agent.spawn per
 │    judgment step, $.ui.ask for gates, tool.call write-gate wrapper,
 │    ui.render side pane from `orchestrator status --json`
 └─ $.process.run → `orchestrator` CLI
        └─ engine: dispatch, validate, record, artifacts, gates, idempotency
              └─ SQLite / Postgres (run doc as JSON column + step_history index)

Codex plugin: same CLI via skill + agents/ mirror (no hooks; enforcement is CLI-side)
Headless (cron/CI): CLI runs the step itself with the same payload,
                    via the Anthropic API or via `claude -p` (see §headless)
```

---

## 3. CLI protocol

Replaces `next` / `done` + exit codes 0-3.

| Verb                        | Signature                                                         | Returns                                 |
| --------------------------- | ----------------------------------------------------------------- | --------------------------------------- |
| `start`                     | `orchestrator start <recipe> <slug> --inputs '{...}' --json`      | `{run_id, slug, next}`                  |
| `step`                      | `orchestrator step <run> --json`                                  | `{status, kind, step_id, payload}`      |
| `done`                      | `orchestrator done <run> <step_id> --out '{...}' --usage '{...}'` | ack                                     |
| `approve`                   | `orchestrator approve <run> <token> [--edits '{...}']`            | resumes blocked run                     |
| `resume`                    | `orchestrator resume <run> "<text>" --json`                       | answers an await_input step             |
| `cancel`                    | `orchestrator cancel <run>`                                       | aborts run, cancels pending gates       |
| `status`                    | `orchestrator status <run> --json`                                | nodes, attempts, artifacts, cost, gates |
| `events`                    | `orchestrator events <run> --since <ts> --json`                   | event stream                            |
| `validate`                  | `orchestrator validate <recipe> [--json]`                         | wiring, in/out, gates-before-writes     |
| `doctor` / `graph` / `pack` | unchanged in spirit                                               | `pack --target claude\|codex` (Phase 4) |

`step` executes every consecutive exec step internally and returns only at a
judgment or gate step — this minimizes subprocess spawns from the harness
(e.g. the Claude Mod).

### await_input

A step may park the run on a question instead of finishing: it records
`status: await_input` with an `ask` and, optionally, a list of labeled
`options`. `step` then reports:

```json
{
  "status": "needs_you",
  "kind": "judgment",
  "step_id": "review",
  "payload": {
    "ask": "Review passed. Ship it, or send back?",
    "options": [
      { "label": "approve" },
      { "label": "rework", "reset_to": "rework" }
    ]
  }
}
```

Polling a parked run re-reports the same question and never re-runs the step.
`orchestrator resume <run> "<text>"` clears it. An answer matching an option —
by label, by the label's first word, or by 1-based number — is applied by the
engine: it advances the run, or resets the DAG to that option's `reset_to`,
without re-dispatching the step that asked. Text matching nothing is handed to
that step on its next dispatch (an exec step reads
`ORCHESTRATOR_USER_DIRECTION`; a judgment step finds it appended to its
prompt), so a free-form answer reaches whoever asked. `resume` returns
`{status, matched, next}`, where `next` is the same shape `step` returns.

This is not the gate mechanism: a gate is a human authorizing a _write_ and is
answered with `approve <run> <token>`, while await*input is a step asking a
\_question* it needs answered to continue.

### `status` enum

| Value       | Meaning                                |
| ----------- | -------------------------------------- |
| `ready`     | step is dispatchable now               |
| `running`   | step in progress                       |
| `done`      | run complete                           |
| `blocked`   | waiting on a gate token                |
| `needs_you` | needs a decision the engine can't make |
| `error`     | run failed                             |

### `kind` enum

| Value      | Meaning                                         |
| ---------- | ----------------------------------------------- |
| `exec`     | script step, engine runs it directly            |
| `judgment` | agent/subagent step, harness must spawn a model |
| `gate`     | signoff step, harness must present for approval |

---

## 4. Step payload (judgment)

Returned by `orchestrator step <run> --json` when `kind: judgment`:

```json
{
  "status": "ready",
  "kind": "judgment",
  "step_id": "design",
  "model": "strong",
  "max_turns": 40,
  "tools": ["fs.read", "fs.write", "shell.test"],
  "system": "<SKILL.md with {in.*}/{out.*} templated + learnings>",
  "in": { "discovery": "/abs/path/discovery.md" },
  "out": { "design": "/abs/path/design.md", "tasks": "/abs/path/tasks.yaml" },
  "out_schema": { "complexity": { "enum": ["XS", "S", "M", "L", "XL"] } },
  "cwd": "/abs/worktree"
}
```

`model` here is the **alias** (`strong|standard|fast|code`), resolved by the
harness/headless driver to a concrete vendor model — never a vendor model id
threaded from the contract (contracts don't carry `model:` — see §6).

---

## 5. `done` payload and the usage guard

```
orchestrator done <run> <step_id> --out '{...}' --usage '{...}'
```

- `--out` — JSON validated against the step's `out` schema.
- `--usage` — required for every completed judgment step. Must carry
  `usage.input_tokens` and `usage.output_tokens`, **both ≥ 1**.

This is not new in v2 — it's the existing `record.py` rule
(`_validate_agent_usage`, reason code `agent_step_missing_usage`,
`orchestrator_next/record.py:389-420`): a completed agent step with no
usable token counts in `usage` is rejected (exit 3) rather than silently
recorded. v2 keeps this rule verbatim; `done --usage` is just the new verb
surface for it. `ORCHESTRATOR_SKIP_USAGE_CHECK` remains an escape hatch for
tests/fixtures, not for real runs.

### `--status abandoned`

A judgment step that cannot do its job records `--status abandoned` with a
`reason`. It skips `out` validation, because there is nothing to validate: an
abandoned step wrote none of its declared artifacts.

Abandoning is therefore **not** a way to complete a node. The node ends with
its own terminal status, `abandoned`, which is neither ready (so it is never
re-dispatched) nor completed (so its dependents stay blocked and never run
against artifacts that do not exist). Routing then applies:

- the node's `on_failure` edge, if it declares one, bounded by `max_retries`
  exactly as a rejected verdict is;
- otherwise the run parks at `status: needs_you` carrying
  `needs_you_reason: "<step> abandoned: <reason>"`, and `step` returns
  `needs_you` with no `ask`. The engine has no question — it has a dead end
  only a human can resolve.

A phase whose remaining nodes all sit behind an abandoned one reports
`needs_you`, never `done`. Reporting success there would hand the harness a
run that produced nothing.

---

## 6. Contract shape

```yaml
id: design
version: 3
kind: judgment            # exec | judgment | gate
max_turns: 40
tools: [fs.read, fs.write, shell.test]
side_effects: []          # e.g. [write:git, write:ticket]
in:
  discovery: {artifact: discovery.md}
  ticket:    {artifact: ticket-context.md, optional: true}
out:
  design:    {artifact: design.md}
  tasks:     {artifact: tasks.yaml, validate: validate-tasks-yaml.sh}
  complexity:{type: enum, values: [XS,S,M,L,XL]}
```

### `fail_on:` — a step that judges its own subject

An enum out may name the values that mean the step judged its subject
unacceptable:

```yaml
out:
  design: { artifact: design.md }
  verdict: { type: enum, values: [pass, needs_work], fail_on: [needs_work] }
```

Reporting one of those values routes through the node's `on_failure` edge
instead of advancing, bounded by `max_retries` — the step itself ran fine and
is recorded `completed`, but the _workflow_ must not carry forward work the
reviewer just rejected. With no `on_failure` edge the node ends terminal and
the run parks at `needs_you`, so a resume cannot advance past a rejection
either.

`fail_on:` is opt-in and validated at load: it requires `type: enum`, must be
a list, and every value must appear in that out's `values:`. A plain enum out
behaves exactly as before. Signoff gates read the same declaration (§7), so
the router and the gate can never disagree about what "rejected" means.

No `model:` key in the contract. The alias lives in `models.yaml` under
`step_models:`. This is already enforced today: `parser.py:237-241`
(`_resolve_agent_instruction`) raises `ContractError` if a contract sets
`model:`, and `model_routes.resolve_step_alias` implements the alias
mapping via `step_models:` in `models.yaml` (principle 7). v2 makes `kind`,
`in`, `out`, `tools`, `side_effects`, `max_turns` load-bearing where v1
treated `kind` as decorative.

---

## 7. Recipe shape

```yaml
name: feature
version: 4
artifacts_root: spec/changes/{slug} # default .orchestrator/runs/{slug}/artifacts
inputs: { ticket: { type: string } }
steps:
  - check-rerun
  - create-worktree
  - load-ticket-context
  - explore
  - design
  - { id: design-review, on_failure: design }
  - { gate: design-signoff, show: [design, tasks], approve_as: impl_token }
  - { id: implement, on_failure: design, requires: impl_token }
  - { id: code-review, on_failure: implement, max_retries: 8 }
  - learn
  - workflow-report
```

### Gate token flow

1. A `gate` step (`{ gate: design-signoff, show: [...], approve_as: X }`)
   sets the run to `status: blocked` and issues a token bound to `X`.
2. `orchestrator step` reports `kind: gate`; the harness renders `show:`
   artifacts for the human and waits for a decision.
3. `orchestrator approve <run> <token> [--edits '{...}']` resumes the run
   and binds the token to `X` for any downstream step declaring
   `requires: X`.
4. `orchestrator cancel <run>` aborts instead.
5. A step with non-empty `side_effects` containing `write:*` must sit behind
   a gate: either some `{gate: ...}` entry appears earlier in the recipe, or
   the step declares `requires: <token>` naming an earlier gate's
   `approve_as`. Otherwise `validate` fails. A `requires:` naming no upstream
   gate, and a gate with no `approve_as`, are also errors.

   `write:workspace` is the one exempt value. It names the writes that
   provision the run's own workspace — worktree create and remove, state
   archive — which cannot sit behind a gate because they build the directory
   the gate's artifacts live in. `write:git` on the same step still needs an
   approval upstream; the exemption covers the value, not the step. Protocol v1's
   `signoff_policy` has no reader left in the engine and is ignored entirely:
   declare a `{gate: ...}` entry instead. Synthesizing a gate the author never
   wrote would park runs at a step nobody expects.

Token state lives on the run under `gates:`, one record per gate
(`{token, token_name, gate_id, issued_at, status, approved_at?, edits?}`).
`step` at a gate is idempotent — polling a blocked run re-returns the token
already issued, never a second one. A step whose `requires:` token is not yet
approved reports `status: needs_you`, not `blocked`: the engine has nothing
left to decide. `status --json` carries `gate_token` (the most recently
approved token) and the full `gates` list.

### Gate trust

A gate exists to put a human behind the work, not to rubber-stamp whatever
happens to be on disk. Each `show:` artifact in the preview therefore carries
its provenance, taken from the per-node artifact records the engine already
keeps:

| Field             | Meaning                                             |
| ----------------- | --------------------------------------------------- |
| `produced_by`     | the node that declares the artifact as an output    |
| `producer_status` | that node's status (`completed`, `abandoned`, …)    |
| `attempts`        | how many times it ran                               |
| `written_by`      | the last node that actually wrote the file          |
| `last_verdict`    | the most recent verdict recorded while producing it |

`written_by` is distinct from `produced_by` on purpose. A reviewer whose
contract declares `out: <the thing it reviews>` — the usual shape for a step
that rewrites a design with its findings — is the last writer, so the preview
says so rather than presenting its rejection notes as the design.

The gate **refuses to mint**, returning `needs_you` with the reason, when any
`show:` artifact has no producer, its producer is not `completed`, or its
`last_verdict` is a value some contract's `fail_on:` calls a failure. A token
minted before a rejection landed is cancelled rather than left approvable; the
gate mints a fresh one once the work is trustworthy again.

---

## 8. Run layout

```
<repo>/.orchestrator/runs/<slug>/
  state.yaml          # run_id (ULID), recipe, sha, nodes[{id,status,attempts}], history
  artifacts/          # declared outputs (or artifacts_root if set)
  scratch/            # gitignored: prompts, stdout, tmp
```

`artifacts/` holds named outputs referenced via `{out.y}`; `scratch/` is
discarded on archive (Phase 2.4).

---

## 9. Config knobs

| Variable                    | Purpose                                                                                                |
| --------------------------- | ------------------------------------------------------------------------------------------------------ |
| `ORCHESTRATOR_STATE_URL`    | Postgres connection string for run state; unset → SQLite default (already on branch, `state_store.py`) |
| `ORCHESTRATOR_MAX_PARALLEL` | caps concurrent step dispatch (`dispatch.dispatch_batch`)                                              |

TBD (Phase 3): `tenant_id` scoping knob, PII redaction config, trust config
(`~/.orchestrator/trust.toml`) — not yet specified beyond the plan's mention.

---

## 10. Migration from v1

`docs/pack-convention.md` protocol **1 → 2**. v1's layout and versioning
scheme (`pack.yaml`, `steps/<id>/contract.yaml` + `prompt.md`/`script.sh`,
`protocol: N` gate on `config pull`) is unchanged. What changes:

| v1                                                                                           | v2                                                                                                                                                  |
| -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `contract.yaml` minimal shape: `id`, `version`, exactly one of `model:`/`run:`               | `contract.yaml` adds load-bearing `kind`, `in`, `out`, `tools`, `side_effects`, `max_turns`; `model:` and v1's `skill:` are rejected                |
| `kind:` field, if present, decorative and ignored                                            | `kind` is required and validated: `exec \| judgment \| gate`                                                                                        |
| Agent step ends output with `COMPLETION:` YAML block; malformed/missing → retryable `failed` | `done --out '{...}' --usage '{...}'` structured JSON, validated against `out` schema; `parse_completion.py` deleted (Phase 1.3)                     |
| `orchestrator next <state.yaml>` dispatches next step                                        | `orchestrator step <run> --json` returns full payload; batches consecutive exec steps                                                               |
| `orchestrator done <state.yaml>` records JSON on stdin                                       | `orchestrator done <run> <step_id> --out --usage` (explicit flags, not stdin)                                                                       |
| Exit code `0` + JSON with model → execute instruction, then `done`                           | `status: ready, kind: judgment` in the `step` response                                                                                              |
| Exit code `0` + script JSON / no agent → script already ran, loop                            | `status: ready, kind: exec` (or already advanced — `step` batches these internally)                                                                 |
| Exit code `1` → workflow complete                                                            | `status: done`                                                                                                                                      |
| Exit code `2` → blocked (signoff)                                                            | `status: blocked` + gate token (see §7)                                                                                                             |
| Exit code `3` → error                                                                        | `status: error`                                                                                                                                     |
| `model: <alias>` in `contract.yaml`                                                          | removed from contract; alias moves to `models.yaml` `step_models:` (principle 7, already implemented via `model_routes.resolve_step_alias`)         |
| No `tools:`/`side_effects:` vocabulary                                                       | `tools:` (capability list) and `side_effects:` (e.g. `write:git`) become contract fields, consumed by validation and the Mod's write-gating wrapper |

**Migration complete.** The v1 verbs are removed: `next`, the stdin-JSON form
of `done`, the self-driving `orchestrator run`, and `--seed-only` are no
longer CLI surface, and the 0/1/2/3 exit-code protocol is gone with them
(`step` always exits 0 and reports `status`). `orchestrator run` survives only
as the spelling of `run --headless`; without that flag it refuses and points
at `start`. `parse_completion.py` and the `COMPLETION:` block are deleted, so
a judgment contract must declare `out:` — `validate-workflow` now errors on
one that does not, where it used to warn. `signoff_policy:` is no longer read
or warned about; declare a `{gate: ...}` entry instead.

---

## 11. Headless backends

`orchestrator run --headless` and `orchestrator headless <run>` walk the same
`step` / `done` verbs a harness walks; the only difference is that the engine
runs each `kind: judgment` step itself. Two backends do that.

| Backend      | Runs the step via                   | Credential                       | Tools                                             |
| ------------ | ----------------------------------- | -------------------------------- | ------------------------------------------------- |
| `anthropic`  | Anthropic Messages API (vendor SDK) | `ANTHROPIC_API_KEY` / auth token | the four built-ins in `headless.TOOL_DEFS`        |
| `claude-cli` | `claude -p` (Claude Code)           | the machine's Claude Code login  | Claude Code's own, allow-listed from the contract |

Selection order, first hit wins: `--backend`, then
`ORCHESTRATOR_HEADLESS_BACKEND`, then `anthropic` if an API credential is in
the environment, else `claude-cli`. Defaulting to `claude-cli` is what lets a
workstation with Claude Code signed in run headless with no API key at all;
an unknown backend name is rejected before a run is seeded.

This does not weaken principle 1. A harness-driven run still never calls a
model: only `headless.drive` ever builds a step runner, and it does so in its
own process. Nothing in the engine's dispatch path can reach a model.

### The `claude-cli` argv

Built per judgment payload by `headless.build_cli_argv`:

```
claude -p
  --output-format json
  --model <payload.model_id>
  --max-turns <payload.max_turns + 3>
  --permission-mode acceptEdits
  --no-session-persistence
  --system-prompt <payload.system>
  --json-schema <schema from payload.out + payload.out_schema>
  [--allowedTools <mapped from payload.tools>]
  [--max-budget-usd $ORCHESTRATOR_STEP_BUDGET_USD]
```

The step's instruction goes in on stdin and `cwd` is `payload.cwd`.

- **Turn headroom.** `--json-schema` spends a turn of its own emitting the
  structured result, and Claude Code counts a wrap-up turn too. The contract's
  `max_turns` is the budget for _work_, so three turns are added before it
  becomes the CLI's cap. Without this a `max_turns: 1` step always returns
  `error_max_turns`.
- **Tools.** `payload.tools` capabilities map through `headless.CLI_TOOL_MAP`,
  which is asserted equal to `pack_export.TOOL_MAP` by a test — one capability
  must mean one tool surface whoever runs the step. Unknown capabilities are
  dropped, never invented. A step declaring no tools gets no `--allowedTools`.
- **Result.** The parsed object is `structured_output`; the trailing fenced
  ```json block of `result` is the fallback for a CLI that did not emit one.
- **Usage.** `usage` supplies the token counts. The model recorded is the
  first `modelUsage` key, which is the dated id the run actually billed
  (`claude-haiku-4-5-20251001`), falling back to the requested `model_id`.
  `total_cost_usd` is carried through as `cost_usd_reported`.
- **Failure.** A non-zero exit or `is_error: true` raises `HeadlessError` with
  the CLI's own `errors`/`subtype` or the stderr tail. A missing `claude`
  binary and a logged-out CLI each get their own message, and the binary is
  checked before the first step so neither surfaces mid-run.
