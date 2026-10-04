# orchestrator

A workflow engine that is a **pure function**:

```
workflow config  +  the step that just ran and how it went  →  what runs next
```

It keeps no state, spawns no processes, and calls no models. There is no run
database, no history, no attempt counters, no gate tokens. Everything that
happens is done by a **driver** — an agent, a script, whatever you like — that
calls this CLI, does the work it names, and reports back.

## Install

```bash
uv tool install git+https://github.com/ugudlado/orchestrator.git   # `orchestrator`
# or, from a checkout:
python -m orchestrator_next
```

## The one verb

```
Usage:
  orchestrator next <workflow> --config PATH [--slug S]
      [--after STEP (--status completed|failed|abandoned
                     | --exit-code N [--stdout-file F])
       [--out JSON] [--attempt N]]
```

`--config` is the pack root. `--slug` names the run (it fills `{slug}` in the
pack's `artifacts_root`). With no `--after` you get the workflow's first step;
otherwise you report what the named step did and get the next one.

Output is always JSON, always exit 0 for a protocol answer (`ready`, `done`,
`needs_you`, `error`); exit 3 is a usage or infrastructure error.

### `status: ready` — a step to run

Every `ready` answer is `{status, kind, step_id, route, payload}` (plus
`recorded` once you are reporting steps). `route` says which rule fired:
`next`, `on_failure`, `reset_to`, `retry`.

**`kind: exec`** — run `payload.run_path` yourself, with `payload.env` merged
over your own. Payload: `run_path`, `step_dir`, `state_mutating`, `in`, `out`,
`out_schema`, `tools`, `side_effects`, `requires`, `env`. The `env` block is
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

**Artifact paths are relative.** `in`, `out` and a gate's `show` are relative
to the driver's working tree, because a run's artifacts live in its worktree
and only the driver knows where that is. Run the CLI from that tree.

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

## Driving it

The protocol is small but has sharp edges (attempt counting, gate tokens,
worktree paths). [`skills/drive/SKILL.md`](skills/drive/SKILL.md) is a skill
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
```

Packs are installed by a separate tool and handed to the engine with
`--config`. The engine never fetches one.
