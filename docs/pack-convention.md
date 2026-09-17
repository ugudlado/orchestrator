# Config Pack Convention

**Protocol version: 2**

A config pack is a directory of workflow schemas + step directories that the
orchestrator engine can dispatch. This doc is the contract a pack author
targets — it doesn't require reading engine source. It documents behavior
that already exists in code; this doc is the authoring surface, dispatch is
the enforcement.

Protocol v2 is specified in [`protocol-v2.md`](protocol-v2.md); that doc is
normative where the two disagree. What changed from v1: `kind`, `in`, `out`,
`tools`, `side_effects`, and `max_turns` became load-bearing contract keys,
and a judgment step reports structured JSON (the v1 `COMPLETION:` block is gone).

## 1. Layout

```
<pack-root>/
  pack.yaml              # name, version, description, protocol: 2
  workflows/*.yaml        # workflow schema definitions
  steps/<id>/
    contract.yaml         # id, version, kind, in/out, prompt|run
    SKILL.md               # judgment steps (prompt.md also accepted)
    script.sh               # exec steps
  steps/lib/              # optional, shared shell helpers (this pack only)
```

A pack may ship its own `steps/lib/`. Depending on another pack's `lib/`
(including the bundled `core` pack's) is undocumented and unsupported.

## 2. `contract.yaml` keys

- `id` — must match the step's directory name.
- `version` — integer, bumped on any change to the contract's behavior.
- `kind` — `exec` | `judgment` | `gate`. Inferred when absent (`run:` →
  `exec`, `prompt:` → `judgment`); an explicit value that contradicts the
  payload is an error. v1's `agent` / `script` spellings still read as
  `judgment` / `exec`.
- Exactly one payload, except on a gate:
  - `prompt: SKILL.md` — judgment step. Resolved inside the step dir first,
    then the skills search path.
  - `run: script.sh` — exec step.
  - a `kind: gate` step has neither; it is metadata the engine renders for a
    human.

**`model:` is rejected.** The capability alias for a step lives in
`models.yaml` under `step_models:`, never in the contract (protocol v2
principle 7). A contract that sets `model:` fails to load.

### Typed I/O

```yaml
id: design
version: 3
kind: judgment
max_turns: 40
tools: [fs.read, fs.write, shell.run]
side_effects: [] # e.g. [write:git, write:ticket, write:workspace]
in:
  discovery: { artifact: discovery.md }
  ticket: { artifact: ticket-context.md, optional: true }
out:
  design: { artifact: design.md }
  tasks: { artifact: tasks.yaml }
  complexity: { type: enum, values: [XS, S, M, L, XL] }
```

- `in:` / `out:` map a **name** to a spec. Each spec declares either
  `artifact:` (a file, resolved to an absolute path by the engine — the pack
  never writes paths) or `type:` (a value carried in the done payload).
  `optional: true` exempts an entry from enforcement.
- `tools:` is the capability allowlist handed to the harness.
- `side_effects:` names what the step changes outside its artifacts.
  `validate-workflow` refuses a recipe where a `write:*` step has no
  preceding gate:

  | Value             | Meaning                                             | Needs a gate |
  | ----------------- | --------------------------------------------------- | ------------ |
  | `write:git`       | commits, branches, merges in the checkout           | yes          |
  | `write:ticket`    | transitions or comments on the tracker              | yes          |
  | `write:<other>`   | anything else a pack names                          | yes          |
  | `write:workspace` | run infrastructure: worktree create/remove, archive | no           |

  `write:workspace` is exempt because it builds the place the gate's own
  artifacts live — a worktree-create step writes git before any gate could
  exist, so requiring one would make every recipe unstartable.

- `max_turns:` caps a judgment step's tool-use loop.
- On a gate: `show:` (artifact names to render) and `approve_as:` (the token
  downstream steps declare via `requires:`).

Optional legacy keys: `state_mutating`, `default_outputs`,
`required_outputs_for_completed`. The last two apply only to a step that has
**not** declared `out:` — a migrated step is validated against `out:` instead.
Any other key is ignored by the engine.

## 3. Step protocol

**Exec steps**

- Exit 0 = success, nonzero = failure (retried per routing policy).
- Environment provided: `REPO_ROOT`, `CHANGE_ID`, `STATE_YAML_PATH`, and
  others per the engine's step-env contract.
- **The last line of stdout must be a JSON object** — its keys become step
  outputs.
- `orchestrator step` runs every consecutive exec step internally; the
  harness only ever sees a judgment or a gate.
- Caveat — `state_mutating` steps are recorded `completed` _before_ they
  run. A nonzero exit there aborts the run instead of recording a retryable
  `failed`. Don't put fallible logic behind `state_mutating`; keep it for
  deterministic teardown/bookkeeping only.

**Judgment steps**

- The prompt is assembled from the step's charter plus step context.
- Every judgment contract must declare an `out:` block; `validate-workflow`
  rejects one that does not.
- The step ends with a single fenced `json` block naming its declared values;
  the harness passes that to
  `orchestrator done <run> <step_id> --out '{...}' --usage '{...}'`.
- `--usage` must carry `input_tokens` and `output_tokens`, at least one
  nonzero, or the record is rejected. `ORCHESTRATOR_SKIP_USAGE_CHECK` is a
  test/fixture escape hatch, not a production one.
- A malformed final block, a missing artifact, or a value outside a declared
  enum all become a rejected `done` (exit 3) or a retryable `failed` step —
  never a hang, never a silent pass.

**Gate steps**

- Reported by `orchestrator step` as `status: blocked`, `kind: gate`, with a
  preview of the `show:` artifacts and a freshly minted token. Polling `step`
  re-returns that same token rather than issuing a second one.
- `orchestrator approve <run> <token> [--edits '{...}']` resumes the run and
  binds the token to the gate's `approve_as` name; `orchestrator cancel <run>`
  aborts instead. A step declaring `requires: <that name>` stays undispatched
  (`status: needs_you`) until the approval lands.

**Exit codes**: the v2 verbs (`start`, `step`, `done`, `status`, `events`)
exit 0 and carry the run's condition in the JSON `status` field
(`ready | running | done | blocked | needs_you | error`); exit 3 is an engine
error. The deprecated `run` / `next` / `done <state.yaml>` verbs keep the v1
codes: `1` complete, `2` blocked, `3` error.

## 4. Aliases

Packs speak in capability-tier aliases (`strong`, `standard`, `fast`,
`code`), never concrete model ids. What an alias resolves to on a given
machine is an agent-config concern (`models.yaml` `step_models:` plus
`~/.orchestrator/models.yaml`), not the pack's.

Dispatch refuses to run a step whose alias has no route on the current
machine. That refusal is documented behavior — not a bug in the pack.

## 5. Protocol versioning

`pack.yaml` declares `protocol: 2`. `orchestrator config pull` installs packs
under `<repo>/.orchestrator/<pack>/` and refuses a pack whose protocol the
engine doesn't support.

Bump the protocol integer only on a breaking change to section 2 or 3 above
(contract keys or step protocol semantics) — not for adding new workflows,
steps, or optional fields.

## 6. Learn results

A `learn` step does not write scenarios directly into the pack. It proposes
rows into the engine's `learn_results` table (`run_id`, `step_id`,
`proposed_row`, `accepted`, `created_at`) in the state DB, where they sit
unaccepted until a human — or the pack's own `persist-learnings` step — marks
a row accepted. `orchestrator pack publish-scenarios <pack> [--step <id>]`
then exports every accepted row into
`.orchestrator/<pack>/steps/<step_id>/scenarios/train.jsonl`, deduped by the
sha256 of each row's canonical JSON, so re-running it is idempotent. Runs stay
out of git; only the reviewed scenarios cross back into the pack.
