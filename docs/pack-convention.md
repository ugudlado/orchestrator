# Config Pack Convention

**Protocol version: 1**

A config pack is a directory of workflow schemas + step directories that the
orchestrator engine can dispatch. This doc is the contract a pack author
targets — it doesn't require reading engine source. It documents behavior
that already exists in code; this doc is the authoring surface, dispatch is
the enforcement.

## 1. Layout

```
<pack-root>/
  pack.yaml              # name, version, description, protocol: 1
  workflows/*.yaml        # workflow schema definitions
  steps/<id>/
    contract.yaml         # id, version, model|run, optional flags
    prompt.md              # agent steps
    script.sh               # script steps
  steps/lib/              # optional, shared shell helpers (this pack only)
```

A pack may ship its own `steps/lib/`. Depending on another pack's `lib/`
(including the bundled `core` pack's) is undocumented and unsupported.

## 2. `contract.yaml` keys

The minimal shape dispatch reads:

- `id` — must match the step's directory name.
- `version` — integer, bumped on any change to the contract's behavior.
- Exactly one of:
  - `model: <alias>` — agent step. **Required for agent steps; there is no
    engine default.** The alias must be one of the vocabulary names (e.g.
    `opus`, `sonnet`, `composer`) — never a concrete model id.
  - `run: script.sh` — script step. Dispatch keys off `run:` alone; a
    `kind: script` field, if present, is decorative and ignored.

Optional: `state_mutating`, `default_outputs`. Any other key is ignored by
the engine.

## 3. Step protocol

**Script steps**

- Exit 0 = success, nonzero = failure (retried per routing policy).
- Environment provided: `REPO_ROOT`, `CHANGE_ID`, `STATE_YAML_PATH`, and
  others per the engine's step-env contract.
- **The last line of stdout must be a JSON object** — its keys become step
  outputs.
- Caveat — `state_mutating` steps are recorded `completed` _before_ they
  run. A nonzero exit there aborts the run (exit 3) instead of recording a
  retryable `failed`. Don't put fallible logic behind `state_mutating`; keep
  it for deterministic teardown/bookkeeping only.

**Agent steps**

- The prompt is assembled from `prompt.md` plus step context.
- The agent must end its output with a `COMPLETION:` YAML block.
- A malformed/missing block, or a nonzero subprocess exit, both become a
  retryable `failed` step — never a hang, never a silent pass. A
  non-conforming pack cannot hang the engine.

**Exit codes surfaced by `orchestrator run`**: `1` complete, `2` blocked
(needs user action), `3` error.

## 4. Aliases

Packs speak in capability-tier aliases (`opus`, `sonnet`, `composer`, …),
never concrete model ids. What an alias resolves to on a given machine is an
agent-config concern (`~/.orchestrator/models.yaml`), not the pack's.

Dispatch refuses to run a step whose alias has no route on the current
machine. That refusal (exit 4) is the documented behavior — not a bug in the
pack.

## 5. Protocol versioning

`pack.yaml` declares `protocol: 1`. `orchestrator config pull` installs packs under `<repo>/.orchestrator/<pack>/` and refuses a pack whose protocol the engine doesn't support.

Bump the protocol integer only on a breaking change to section 2 or 3 above
(contract keys or step protocol semantics) — not for adding new workflows,
steps, or optional fields.
