---
feature-id: orc-76
linear-ticket: none
---

# Design: Step-as-directory + typed file I/O on contracts

## Context

Today a step contract is a single mixed YAML file under `config/steps/<id>.yaml`
that bundles three concerns: schema metadata (`id`, `version`, `agent`/`run`,
`inputs`, `outputs`, `depends_on`, `repeat_until`), an English instruction
block, and a growing `rules:` list. For the eight `run:`-style script steps the
script lives separately under `config/scripts/inline/<id>.sh` — a 1:1 pair
split across two directory trees that workflow authors must keep in sync.

The current `inputs:`/`outputs:` fields are *named* values: the dispatcher
resolves an input by scanning `state.step_history[*].evidence.outputs[<name>]`
for the most recent producer (`parser._load_contract`, `dispatch._resolve_inputs`,
`record._check_declared_outputs`). Files produced under
`$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/` are not directly named in the contract —
the contract only declares the *handle* (e.g. `discovery_result`) and the
agent embeds the path in instruction prose. There is no boundary-time check
that the file exists.

This feature reorganises each step into a directory and changes I/O semantics
from named-value handoff to typed file-path references with pre/post existence
validation.

## Goals / Non-Goals

### Goals

- Each dispatch-engine step lives in `config/steps/<id>/` holding exactly two
  files: `contract.yaml` (typed schema) and one payload (`prompt.md` for
  agent steps, `script.sh` for script steps).
- `contract.yaml` carries `kind: agent | script` explicitly — no inference.
- Step scripts move from `config/scripts/inline/<id>.sh` into the step
  directory; non-step helpers stay in `config/scripts/inline/`.
- `inputs:`/`outputs:` become typed file-path objects (`{path, template?}`)
  that the dispatcher pre-checks and `record.py` post-checks via
  `os.path.exists`.
- Backward-compat read path: `parser.py` / `generate_plan.py` continue to
  load the flat `<id>.yaml` form for one cycle so external workflows that
  haven't migrated still load.
- The learn cycle's append target (rules under `contract.yaml`) keeps its
  single-key write semantics.
- `config/steps/contracts/artifact-formats.md` is deleted; producer
  `prompt.md` files carry their own format contract section, and the
  reviewer `prompt.md` carries the consolidated view it needs at review time.

### Non-Goals

- No changes to DAG scheduling, phase gating, retry logic, or the
  `orchestrator done` JSON payload shape.
- No migration of `select-workflow.yaml` (skill-level pre-init, not
  dispatched by the engine).
- No migration of bootstrap scripts (`git-init.sh`, `bootstrap-commit.sh`,
  etc.) — they are not wired to any `config/steps/*.yaml` contract.
- No migration of `validate-tasks-yaml.sh`, `verify-report.sh`, or
  `compute-prediction-accuracy.py` — they are sub-helpers, not step
  entrypoints.
- No changes to the repo-override priority in `_contract_search_dirs`
  beyond extending each search dir to look for the directory form first
  and the flat-file form second.
- No removal of flat-file fixtures in tests this cycle — back-compat
  read path stays.

## Approaches Considered

### Approach 1: Directory-per-step + typed file I/O (one feature, two stages)

Each step becomes `config/steps/<id>/{contract.yaml, prompt.md | script.sh}`.
`contract.yaml` holds only schema (no instruction prose). `kind:` is an
explicit field. Stage A migrates layout (string list `inputs:/outputs:`
preserved); Stage B converts I/O to typed `{path}` objects with existence
checks.

- Pros: aligns directory layout and I/O semantics in one feature so callers
  only re-learn once. Stages are independently green, limiting blast radius.
  Layout migration is mechanical; I/O migration is logical — separating
  them as stages keeps reviews tractable.
- Cons: larger diff than a pure-layout change. More test churn (parser
  fixtures + dispatch + record + generate_plan).
- Complexity: L

### Approach 2: Directory-per-step now, typed I/O as a follow-up feature

Migrate the layout this cycle. Typed I/O lands in a separate feature later.

- Pros: smallest possible blast radius per cycle. Each feature is reviewable
  in one sitting.
- Cons: two cycles of `parser.py`/`dispatch.py`/`record.py` touch points.
  Workflow authors learn the new layout, then re-learn the I/O shape one
  cycle later. The flat-file back-compat read path lingers across two
  cycles instead of one.
- Complexity: M (per feature) — but two features in sequence.

### Approach 3: Single-file contract with embedded payload, no directory

Keep `config/steps/<id>.yaml` as a single file but split internal sections
(`contract:`, `prompt:`, `script:`). The learn cycle appends to a section
inside the YAML; the dispatcher extracts `script:` into a tempfile to exec.

- Pros: no directory churn. Single file remains the contract surface.
- Cons: defeats the co-location goal — script content trapped in YAML
  strings is hard to lint, hard to grep, breaks shell highlighting and
  shebang execution, and complicates `chmod +x`. Inverts the current
  reality where scripts are tracked, runnable files. Two distinct
  payload types (markdown vs bash) in one YAML field is awkward.
- Complexity: M (but with worse ergonomic outcome)

### Selected Approach

**Approach 1** — directory-per-step plus typed file I/O, staged into two
phases inside one feature.

Constraints that rule out the alternatives:

- The brief's intent is to bundle layout and I/O so callers re-learn once
  (rules out Approach 2 unless cycle cost is the dominant concern, which
  it is not — the dispatch surface is small and well-tested).
- Step scripts must remain runnable, lintable shell files (rules out
  Approach 3 — embedded payloads sacrifice tooling for marginal directory
  savings).

## High-Level Design

### Architecture Overview

```
config/steps/
  <step-id>/
    contract.yaml           # typed schema only (kind, agent|run, inputs, outputs, rules, ...)
    prompt.md               # iff kind: agent
    script.sh               # iff kind: script
  contracts/                # cross-cutting docs (NOT a step) — done-payload, step-dispatch, etc.
  CONVENTIONS.md
config/scripts/
  inline/                   # non-step helpers ONLY (_read_state_env.sh, append-retro.sh, merge-to-main.sh, etc.)
  orchestrator_next/        # python engine (unchanged location)
```

Three engine modules learn the new layout:

1. `parser.py` — `_load_contract` looks in `<step_id>/contract.yaml` first,
   falls back to `<step_id>.yaml`. For agent kinds the instruction body is
   read from sibling `prompt.md`. For script kinds the `run` field is
   resolved relative to the contract directory.
2. `generate_plan.py` — `_load_step_contract_raw` mirrors the same search.
3. `bin/orchestrator` — `_run_path` resolution prefers the contract-dir
   sibling, then falls back to the legacy `config/<run-path>`.

`record.py` and `dispatch.py` learn typed-I/O semantics: declared
inputs/outputs are file paths checked via `os.path.exists` against
`$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`.

### Key Abstractions

- **`StepContract`** — `kind: Literal["agent","script"]` added; `inputs` and
  `outputs` become `list[InputSpec]` / `list[OutputSpec]` where each spec is
  `{name: str, path: str, optional: bool}`. The `path` may contain a
  `<slug>` placeholder substituted at dispatch time from
  `state.change_id`.
- **Contract directory** — the directory holding `contract.yaml` is the
  resolution root for the contract's `run:` and for `instruction_file`
  (an implicit `prompt.md` lookup for agent kinds).
- **Backward-compat read path** — when only the flat `<id>.yaml` exists,
  the loader synthesizes `kind` (`script` if `run:` set, else `agent`),
  reads `instruction:` from the flat YAML, and treats `inputs:/outputs:`
  as legacy named handles (string list). The same `StepContract`
  dataclass is returned either way.

### `<slug>` substitution

Typed paths in `contract.yaml` use the literal token `<slug>` where the
feature id appears. Resolution happens once at dispatch time:

```yaml
# config/steps/design-and-draft-artifacts/contract.yaml
kind: agent
agent: architect
inputs:
  - name: discovery
    path: spec/changes/<slug>/discovery.md
outputs:
  - name: design
    path: spec/changes/<slug>/design.md
  - name: tasks
    path: spec/changes/<slug>/tasks.yaml
```

Substitution rule: `<slug>` → `state.change_id`. Resolved paths are
absolute when joined with `state.worktree_artifact_dir` (already the
join base used by `_check_declared_outputs`).

## Low-Level Design

### Components

**`parser.py`**

- `_load_contract(step_id, state_yaml_path)` extended:
  1. For each dir in `_contract_search_dirs`, look first for
     `{step_id}/contract.yaml`, then for `{step_id}.yaml`.
  2. If `contract.yaml` is found, also read the sibling payload:
     - `kind: agent` → read `prompt.md`; raise `ContractError` if absent.
     - `kind: script` → record `run` as `{contract_dir}/{run}` (relative
       form) or absolute when so written; raise `ContractDispatchError`
       if the script is absent.
  3. Parse `inputs:/outputs:` items: a typed item is a mapping with a
     `path:` key; a legacy string item is preserved as the named handle.
     A typed item also accepts `optional: true`.
  4. Resolve `<slug>` against `state.change_id` only when the caller
     supplies `state` (i.e., at dispatch time). At schema-load time
     `_load_step_contract_raw` may see raw `<slug>` strings — that is
     fine; substitution happens later.

- `StepContract` dataclass changes:
  - New field `kind: str` (`"agent"` | `"script"` | legacy `""`).
  - `inputs` and `outputs` become `list[dict[str, Any]]` (each item:
    `{name, path?, optional?}`). The old `list[str]` shape is preserved
    in a sidecar `legacy_input_names` / `legacy_output_names` field for
    the back-compat read path consumed by `dispatch._resolve_inputs`.

**`dispatch.py`**

- `_resolve_inputs(state, contract)` — extended:
  - For each input spec with `path:`, resolve `<slug>` → `state.change_id`,
    then `os.path.join(state.worktree_artifact_dir, resolved_path)`.
  - Return `{name: resolved_path}` (string path) so the existing
    `step_context.inputs` shape is preserved (agents still get a dict).
  - Legacy string-named inputs continue to resolve from `evidence.outputs`.

- `_check_required_inputs(state, contract, step_id)` — extended:
  - For typed inputs with `path:` set, the missing condition is
    `not os.path.isfile(resolved_path)`. `optional: true` never blocks.
  - For legacy inputs the existing `_resolve_inputs` walk is unchanged.
  - The diagnostic message names the file path that was missing (not the
    handle name) so the operator can act directly.

**`record.py`**

- `_check_declared_outputs(declared, outputs, state_raw)` — extended:
  - When `declared` is a list of typed specs (each `{name, path}`), the
    satisfaction check is `os.path.exists(resolved_path)` with the same
    `worktree_path → repo_root` base resolution already implemented for
    path-named outputs. The `evidence.outputs[name]` key may still be
    present but is no longer required.
  - The legacy "name contains `/`" heuristic stays for back-compat; it
    becomes a no-op once all callers have migrated.

- `append-retro.sh` path — fixed to `config/scripts/inline/append-retro.sh`
  (the current stale `scripts/inline/` lookup at line 1662).

**`generate_plan.py`**

- `_load_step_contract_raw(step_id, state_yaml_path)` — extended to look
  for `{step_id}/contract.yaml` first, then `{step_id}.yaml`. Returns
  the raw YAML dict either way. `_build_step_block` reads `rules` and
  `repeat_until` from the same dict.

**`bin/orchestrator`**

- `_run_path` resolution (lines 359–363) — extended:
  - If `action["run"]` is non-absolute and the step's `contract.yaml`
    sits in a step directory, resolve against the contract directory:
    `{step_contract_dir}/{run}`.
  - Else fall back to the current `$ORCHESTRATOR_HOME/config/{run}` join.
  - `dispatch.py` already passes the contract path to the action via the
    existing `action["step_contract_dir"]` channel (added as part of
    this work — small addition to the dispatch action dict).

### Data Flow

```
orchestrator next                       orchestrator done
        │                                       │
        ▼                                       ▼
   dispatch.py                            record.py
        │                                       │
parser._load_contract                _check_declared_outputs
        │                                       │
search dirs →                          for each typed output:
  <id>/contract.yaml ──┐                 os.path.exists(resolved_path)
  <id>.yaml (legacy) ──┘                       │
        │                                  evidence.outputs (legacy)
read prompt.md / script.sh
substitute <slug>
        │
_check_required_inputs:
  for each typed input:
    os.path.isfile(resolved_path)
```

### State Management

No new state. `state.yaml` shape is unchanged. The only behaviour change
at state boundaries is what `_check_declared_outputs` accepts as
"satisfied" — file existence becomes the truth signal for path-typed
outputs in addition to `evidence.outputs` presence.

### Error Handling

| Failure | Where | Outcome |
|---|---|---|
| Step directory missing | `_load_contract` | `FileNotFoundError` with searched dirs listed (UC-E1) |
| `contract.yaml` present, `prompt.md` missing (kind: agent) | `_load_contract` | `ContractError("agent contract <id> missing prompt.md")` (UC-E2) |
| `contract.yaml` present, `script.sh` missing (kind: script) | `_load_contract` | `ContractDispatchError("script contract <id> missing script payload")` (UC-E5) |
| Typed input file absent at dispatch | `_check_required_inputs` | exit 2, diagnostic names the missing path (UC-E3) |
| Typed output file absent at record | `_check_declared_outputs` | exit 3, `reason: "missing_outputs"`, list names the path (UC-E4) |
| `kind:` missing in `contract.yaml` | `_load_contract` | `ContractError("contract <id> missing kind: field (agent|script)")` |

Back-compat fallback path: when the flat-file form is found, `kind` is
synthesized (`script` if `run:` set else `agent`) and the
file-existence checks degrade to the legacy `evidence.outputs` check.

## Constraints

- The dispatch boundary contract in `done-payload.md` and
  `step-dispatch.md` must remain unchanged. This feature changes how
  contracts are loaded and how I/O is validated — not what the
  `orchestrator done` JSON looks like.
- The repo-override mechanism (`$REPO_ROOT/.orchestrator/steps/`) must
  follow the same directory layout. `_contract_search_dirs` order is
  unchanged.
- `select-workflow.yaml` stays as a flat YAML; it is invoked by
  `skills/orchestrate` outside the dispatch engine and must continue to
  load with the existing skill-side reader.

## Trade-offs

- **Larger diff, smaller cycle count**. We migrate 16 step contracts +
  parser + dispatch + record + generate_plan + orchestrator binary +
  tests in one feature. The alternative (Approach 2) splits this into
  two cycles and re-touches the same engine modules twice. We pay the
  bigger diff to avoid the bigger total churn.
- **Back-compat read path lingers for one cycle**. The flat-file form
  still loads. This is the smallest stable migration: external workflows
  and old fixtures don't break the moment this lands. A follow-up
  cleanup feature can delete the back-compat branch once we are
  confident.
- **Format contracts duplicated into the reviewer prompt**. Deleting
  `artifact-formats.md` and pushing contracts to producer prompts is
  cleaner per-step but forces the reviewer prompt to carry a
  consolidated copy. Cost: one file's worth of duplication. Benefit:
  no runtime indirection (the reviewer doesn't load five producer
  prompts at review time just to read their format sections).

## Acceptance Criteria

- AC-1: Given a step `<id>` with `config/steps/<id>/contract.yaml` and
  sibling `prompt.md`, when `parser._load_contract(<id>, ...)` runs,
  then it returns a `StepContract` whose `kind == "agent"` and whose
  `instruction` equals the contents of `prompt.md`. [traces: UC-1]

- AC-2: Given a step `<id>` with `config/steps/<id>/contract.yaml`
  declaring `kind: script` and `run: script.sh`, when the dispatcher
  builds the action, then `action["run"]` resolves to the absolute
  path `config/steps/<id>/script.sh` and the file is executable.
  [traces: UC-2]

- AC-3: Given `design-and-draft-artifacts/contract.yaml` declares
  `inputs: [{name: discovery, path: spec/changes/<slug>/discovery.md}]`,
  when `dispatch._check_required_inputs` runs with `state.change_id =
  "orc-99"` and the file `spec/changes/orc-99/discovery.md` is absent,
  then dispatch exits 2 with a diagnostic naming the resolved path.
  [traces: UC-3, UC-E3]

- AC-4: Given a step declares `outputs: [{name: design, path:
  spec/changes/<slug>/design.md}]`, when `orchestrator done` is called
  with a payload whose `evidence.outputs.design` value is present but
  the file at the resolved path does not exist, then `record.py`
  rejects with exit 3 and `reason: "missing_outputs"`. [traces: UC-4,
  UC-E4]

- AC-5: Given the learn cycle wants to append a new rule to step `<id>`,
  when it edits the contract, then the rule lands as a new item in
  `config/steps/<id>/contract.yaml`'s `rules:` list and `prompt.md` is
  untouched. [traces: UC-5]

- AC-6: Given a step directory contains only `contract.yaml` (no
  payload), when `_load_contract` runs, then it raises `ContractError`
  for `kind: agent` (missing `prompt.md`) or `ContractDispatchError`
  for `kind: script` (missing `script.sh`). [traces: UC-E2, UC-E5]

- AC-7: Given a step contract still in the flat-file form
  (`config/steps/<id>.yaml`), when `_load_contract` runs, then it
  successfully returns a `StepContract` with `kind` synthesized from
  the presence of `run:`. [traces: UC-1, UC-2]

- AC-8: After migration, `find config/scripts/inline -name '<id>.sh'`
  for each migrated script step returns no result — the script lives
  only in `config/steps/<id>/script.sh`. Non-step helpers
  (`_read_state_env.sh`, `merge-to-main.sh`, `remove-worktree.sh`,
  `append-retro.sh`, `validate-tasks-yaml.sh`, `verify-report.sh`,
  `compute-prediction-accuracy.py`) remain in `config/scripts/inline/`.

- AC-9: Given a feature whose discovery step writes
  `spec/changes/orc-76/discovery.md`, when the architect step dispatches
  and the file exists, then `step_context.inputs.discovery` carries the
  absolute resolved path string (worktree_artifact_dir joined). [traces:
  UC-3]

- AC-10: `config/steps/contracts/artifact-formats.md` is deleted from
  the tree; each producer step's `prompt.md` carries the section for
  the artifact it produces; `run-phase-review/prompt.md` carries the
  consolidated format-contract reference. [traces: UC-1, UC-5]

- AC-11: `record.py`'s `append-retro.sh` lookup path is
  `config/scripts/inline/append-retro.sh` — the stale `scripts/inline/`
  path is removed. [traces: UC-5]

## Decisions

- **kind: explicit in contract.yaml** → declaring kind avoids the
  inference ambiguity when both or neither payload file is present →
  parser fails loudly, no silent dispatch on the wrong contract type.

- **Step scripts live in `config/steps/<id>/script.sh`, non-step
  helpers stay in `config/scripts/inline/`** → single canonical location
  per concern (step payload vs cross-cutting helper) without forcing a
  third location → workflow authors edit one directory per step;
  helper authors continue to edit `config/scripts/inline/`.

- **`run:` is relative to the contract directory** → keeps the
  contract self-contained and the resolver one line of code → if the
  contract directory is moved or vendored as a unit, the script
  reference still resolves.

- **`rules:` stays in `contract.yaml`; instruction prose moves to
  `prompt.md`** → the learn cycle already writes structured list items
  with inline metadata comments; YAML list append is mechanically
  simpler than markdown section parsing → no new markdown-section
  parser, no risk of the learn cycle malforming prose.

- **`<slug>` placeholder, not `${slug}` or `$CHANGE_ID`** → matches
  the placeholder already used in docs and discovery brief examples;
  avoids shell-substitution lookalike confusion in YAML.

- **Layout migration and typed I/O bundled into one feature, staged
  as two task groups** → reviewers re-learn the contract surface once;
  each stage is independently green, so the blast radius per task is
  bounded by the stage, not the feature.

- **`artifact-formats.md` deleted; reviewer prompt carries the
  consolidated copy** → producer-owned format contract avoids the
  cross-tree dependency; reviewer keeps a single review-time reference.

## Open Questions

(none — all OQ-1 through OQ-6 resolved in § Decisions and § High-Level
Design)

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
