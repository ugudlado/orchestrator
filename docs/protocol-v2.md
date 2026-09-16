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
Headless (cron/CI): CLI calls model API directly with the same payload
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
| `cancel`                    | `orchestrator cancel <run>`                                       | aborts run                              |
| `status`                    | `orchestrator status <run> --json`                                | nodes, attempts, artifacts, cost        |
| `events`                    | `orchestrator events <run> --since <ts> --json`                   | event stream                            |
| `validate`                  | `orchestrator validate <recipe>`                                  | wiring, in/out, gates-before-writes     |
| `doctor` / `graph` / `pack` | unchanged in spirit                                               | `pack --target claude\|codex` (Phase 4) |

`step` executes every consecutive exec step internally and returns only at a
judgment or gate step — this minimizes subprocess spawns from the harness
(e.g. the Claude Mod).

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
5. A step with non-empty `side_effects` containing `write:*` and no
   preceding gate is a `validate` error (Phase 3.1) — TBD (Phase 3): exact
   validation rule wording.

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

| v1                                                                                           | v2                                                                                                                                                     |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `contract.yaml` minimal shape: `id`, `version`, exactly one of `model:`/`run:`               | `contract.yaml` adds load-bearing `kind`, `in`, `out`, `tools`, `side_effects`, `max_turns`; `model:` is rejected (already true — `parser.py:237-241`) |
| `kind:` field, if present, decorative and ignored                                            | `kind` is required and validated: `exec \| judgment \| gate`                                                                                           |
| Agent step ends output with `COMPLETION:` YAML block; malformed/missing → retryable `failed` | `done --out '{...}' --usage '{...}'` structured JSON, validated against `out` schema; `parse_completion.py` deleted (Phase 1.3)                        |
| `orchestrator next <state.yaml>` dispatches next step                                        | `orchestrator step <run> --json` returns full payload; batches consecutive exec steps                                                                  |
| `orchestrator done <state.yaml>` records JSON on stdin                                       | `orchestrator done <run> <step_id> --out --usage` (explicit flags, not stdin)                                                                          |
| Exit code `0` + JSON with model → execute instruction, then `done`                           | `status: ready, kind: judgment` in the `step` response                                                                                                 |
| Exit code `0` + script JSON / no agent → script already ran, loop                            | `status: ready, kind: exec` (or already advanced — `step` batches these internally)                                                                    |
| Exit code `1` → workflow complete                                                            | `status: done`                                                                                                                                         |
| Exit code `2` → blocked (signoff)                                                            | `status: blocked` + gate token (see §7)                                                                                                                |
| Exit code `3` → error                                                                        | `status: error`                                                                                                                                        |
| `model: <alias>` in `contract.yaml`                                                          | removed from contract; alias moves to `models.yaml` `step_models:` (principle 7, already implemented via `model_routes.resolve_step_alias`)            |
| No `tools:`/`side_effects:` vocabulary                                                       | `tools:` (capability list) and `side_effects:` (e.g. `write:git`) become contract fields, consumed by validation and the Mod's write-gating wrapper    |

Both `next`/`done` and `start`/`step`/`done` coexist during Phase 1 (old
verbs intact per the plan's Phase 1.1); old verbs are removed at the end of
Phase 1 once all 23 steps run end-to-end via the new verbs in headless mode.
