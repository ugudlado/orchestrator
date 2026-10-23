# orchestrator

A workflow engine that is a **pure function**:

```
workflow config  +  the step that just ran and how it went  →  what runs next
```

It keeps no state, spawns no processes, and calls no models. There is no run
database, no history, no attempt counters, no gate tokens. Everything that
happens is done by a **driver** — an agent, a script, whatever you like — that
calls this CLI, does the work it names, and reports back.

This repo has two parts: the **engine** (`orchestrator_next`, `orchestrator`) and a
self-hosted **driver** (`orchestrator_driver`, `orchestrator-driver`) that loops it for you: one
worker process, a Postgres database (`database_url`), nothing else to run. See [Run a workflow end to end](#run-a-workflow-end-to-end).

## Install

```bash
uv tool install git+https://github.com/ugudlado/orchestrator.git   # `orchestrator` and `orchestrator-driver`
# or, from a checkout:
python -m orchestrator_next
```

## The one verb

```
Usage:
  orchestrator next <workflow> --config PATH [--slug S] [--artifacts-dir ABS]
      [--bindings PATH]...
      [--after STEP (--status completed|failed|abandoned
                     | --exit-code N [--stdout-file F])
       [--out JSON] [--attempt N]]
```

`--config` is the pack root. `--artifacts-dir` is an absolute directory the
driver owns (see below). `--slug` names the run (it fills `{slug}` in the
pack's `artifacts_root`). With no `--after` you get the workflow's first step;
otherwise you report what the named step did and get the next one.

Output is always JSON, always exit 0 for a protocol answer (`ready`, `done`,
`needs_you`, `error`); exit 3 is a usage or infrastructure error.

### `status: ready` — a step to run

Every `ready` answer is `{status, kind, step_id, route, payload}` (plus
`recorded` once you are reporting steps). `route` says which rule fired:
`next`, `branch`, `on_failure`, `reset_to`, `retry`.

**`kind: exec`** — run `payload.run_path` yourself, with `payload.env` merged
over your own. Payload: `run_path`, `step_dir`, `state_mutating`, `in`, `out`,
`out_schema`, `tools`, `bindings`, `side_effects`, `requires`, `fingerprint`, `env`. The `env` block is
engine-set only (`ORCHESTRATOR_STEP_ID`, `_STEP_DIR`, `_CHANGE_ID`,
`CHANGE_ID`, `_PROMPT_DIRS`, `_PROMPT_PATH`) — never a copy of yours, so it
cannot leak your secrets into printed output.

**`kind: judgment`** — hand the charter to a model. Same payload, with
`prompt_path` and `max_turns` instead of `run_path`:

```json
{
  "prompt_path": "<pack>/steps/design/SKILL.md",
  "max_turns": 40,
  "in": { "discovery": "spec/changes/demo/discovery.md", "ticket": "…" },
  "out": { "design": "spec/changes/demo/design.md", "tasks": "…/tasks.yaml" },
  "out_schema": {
    "complexity": { "type": "enum", "values": ["XS", "S", "M", "L", "XL"] }
  }
}
```

**`kind: gate`** — show the files and wait for a human:

```json
{
  "step_id": "design-signoff",
  "approve_as": "impl_token",
  "show": { "design": "spec/changes/demo/design.md", "tasks": "…/tasks.yaml" }
}
```

### The other three answers

```json
{"status": "done", "recorded": {…}}
{"status": "needs_you", "step_id": "code-review", "reason": "retries exhausted", "recorded": {…}}
{"status": "error", "step_id": "design",
 "error": "invalid out: out.complexity: 'XXL' not one of ['XS','S','M','L','XL']"}
```

An `error` records nothing — fix the output and report the step again.

## Semantics worth knowing

**Artifact paths.** With `--artifacts-dir /abs`, `in`, `out` and a gate's
`show` are `/abs/<artifact>` (the pack's `artifacts_root` is ignored),
`payload.env` gains `ORCHESTRATOR_ARTIFACTS_DIR`, and answers do not depend on
the CLI's cwd. The flag must be absolute (else exit 3); the engine never
creates the directory. Without it, paths are relative to the driver's working
tree, per `artifacts_root`: run the CLI from that tree.

**exec stdout protocol.** The engine parses the last JSON line of a script's
stdout: `{"status": …, "outputs": {…}}`, or a status plus flat keys, or bare
keys (status defaults to `completed`). `state_patch` is lifted from either
level. All of it is echoed back under `recorded` and **applied to nothing** —
there is nothing to apply it to. A non-zero exit is a failure whose outputs
the engine replaces with `{"reason": "script exited N"}`.

**`fail_on`.** A contract may mark out values as rejections
(`verdict: {type: enum, values: [pass, needs_work], fail_on: [needs_work]}`).
Reporting one routes `on_failure` even if the driver said `completed`, so a
rejected review can never be waved through.

**`checks`.** An artifact out may declare `checks:` (`min_bytes: N`,
`contains: [text, …]`, `parses: yaml | json`). `validate_out` enforces them, so
a failing file is the same `error` answer as a missing one.

**`routes`.** A step entry may branch on its outputs once it has `completed`
(after `fail_on`, so a rejected verdict still goes `on_failure`):
`routes: [{when: {severity: [low, trivial], area: billing}, goto: quick-fix}]`.
A list means "one of", a scalar means "equals", every key must match, and a key
absent from `out` never matches. The first matching case wins (`route: branch`);
none matching falls through to the next entry. `goto` may name any step; one at
or before the step itself is a loop, capped by `max_retries` like `on_failure`.
A case with no `when` is a default that always matches, so only the last case
may be one; it lets branches rejoin (`routes: [{when: {kind: hotfix}, goto: ship}, {goto: review}]`).
A gate has no outputs, so it may declare only a default (`routes: [{goto: ship}]`).
Bad `routes` or `on_failure` targets are an error.

**`fingerprint`.** A contract may opt in with a top-level
`skip_when_unchanged: true`; the step then carries a sha256 of its own files
and its declared `in` artifacts. Every other step gets `null`, meaning never
skip. It is opt-in because the fingerprint covers nothing else: a step that
reads git state, human input or the web would be skipped wrongly, so opt in
only for steps whose result depends on nothing beyond those. The parser
rejects it together with `side_effects`, `state_mutating` or a gate. The
driver may skip a ready step whose fingerprint equals that of the step's last
`completed` record in this run, reporting the previous outputs again with
`--after <step> --status completed --out <previous>`. The engine keeps no
history, so it cannot do this itself.

**`reset_to`.** A failed step may name its own rework target in `--out`, at or
before itself; it wins over the static `on_failure` edge.

**`await_input`.** A judgment enum out may list the reserved value
`await_input`. Reporting it (as `completed` or `failed`) returns `needs_you`
with an `await_input` payload and the same step id, so the driver relays the
question and re-runs that step with the answer. It is checked before output
validation (artifacts may be missing) and wins over `fail_on`, `reset_to`,
`on_failure` and `max_retries`. It can never appear in `fail_on`; the parser
rejects that.

**`requires`.** A step may declare a gate token. It is passed through as data —
with no state the engine cannot know a token was issued, so **the driver must
refuse a step whose token it does not hold.**

**optional outs.** An `out` marked `optional: true` may be omitted. Naming a
path is a claim, and a claim is checked.

**Retries.** `--attempt` is the driver's count of how many times that step has
run, for the whole run; the engine stops at `max_retries`.

## Tool bindings

A contract's `tools:` may name role capabilities `<role>.<verb>` — `ticket.read`,
`ticket.write`, `forge.pr`, `chat.post` — where the role is `ticket`, `forge` or
`chat`. Which product fills a role is a team choice, kept out of the workflow the
way models are. A bindings file says what:

```yaml
roles:
  ticket: { provider: linear, via: mcp, server: linear }
  forge: { provider: github, via: cli, command: gh }
  chat: { provider: slack, via: mcp, server: slack }
```

Each binding needs string `provider` and `via` (`mcp`, `cli` or `script`); any
other string keys pass through verbatim. Layers, lowest to highest: the pack's
`bindings.yaml` (optional), then the repo's `.orchestrator/bindings.yaml`
(optional, picked up automatically, no flag), then each `--bindings PATH` in order
(a driver passes team, then run). The repo layer is read only when the pack is
installed at `<repo>/.orchestrator/<pack>` (the parent directory is named
`.orchestrator`), so a dev checkout like `workflows/config` never picks up a
stray file. The highest layer naming a role owns it wholesale — no field
merging. A missing or invalid `--bindings` file, or an invalid repo file, is
exit 3.

Consumers should commit `.orchestrator/bindings.yaml` and ignore the installed
pack:

```gitignore
.orchestrator/*
!.orchestrator/bindings.yaml
```

Every non-gate `ready` payload carries `bindings`: `{role: binding}` for the roles
that step's `tools` name, `{}` if none. A named role with no binding is `null`:
unbound, so the driver or agent falls back to its own detection; the engine does
not guess. When non-empty, the same dict is in `payload.env` as compact JSON
`ORCHESTRATOR_BINDINGS`.

## Run a workflow end to end

Install a pack ([ugudlado/workflows](https://github.com/ugudlado/workflows)), copy
[`orchestrator_driver/driver.example.yaml`](orchestrator_driver/driver.example.yaml) to `driver.yaml`
and set `pack` and `projects`, then:

```bash
orchestrator-driver serve --config driver.yaml &                      # 1. the worker
orchestrator-driver submit --config driver.yaml --workflow feature --project app \
    --slug add-shout --ticket "add a --shout flag with a test"        # 2. queue a run
orchestrator-driver decide add-shout design-signoff 1 approved --config driver.yaml   # 3. approve gates as they open
```

`workflows` lists what the pack offers, `show` says what a run waits for, `answer` replies to a
question, `stop` kills a run. The driver's config, CLI, database schema, systemd unit and security
notes are in [`orchestrator_driver/README.md`](orchestrator_driver/README.md).
[orchestrator-studio](https://github.com/ugudlado/orchestrator-studio) is the web UI over the
same runs.

## Driving it

Any driver works; the one above is the supported one.

The protocol is small but has sharp edges (attempt counting, gate tokens,
worktree paths). The [`orchestrate`](https://github.com/ugudlado/skills/blob/main/workflow/orchestrate/SKILL.md) skill (skills repo) is a skill
that drives a workflow end to end. A pack may ship its own `DRIVER.md`
describing what its scripts need — see
[`docs/pack-driver-notes.md`](docs/pack-driver-notes.md).

## Pack layout

```text
<pack>/
  workflows/<name>.yaml    steps:, artifacts_root:, inputs:
  steps/<id>/
    contract.yaml          kind, in:, out:, tools, max_turns, run: | prompt:
    SKILL.md               judgment steps: the charter
    script.sh              exec steps: the script
  DRIVER.md                what this pack's scripts need from a driver
  models.yaml              read by the DRIVER, not the engine
  bindings.yaml            optional default tool bindings (see Tool bindings)
```

Packs are installed by a separate tool and handed to the engine with
`--config`. The engine never fetches one.
