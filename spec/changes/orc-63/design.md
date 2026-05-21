---
feature-id: orc-63
linear-ticket: ORC-63
---

# Design: DAG Dispatch Foundation

## Context

The orchestrator dispatcher (`config/scripts/orchestrator_next/dispatch.py`)
selects the next workflow step by scanning `workflow_plan[phase].active` in
declaration order (`_phase_step_ids`, dispatch.py:72–77; the selection loop
at dispatch.py:308–323). Step ordering is implicit list position with no
declared dependency relationships.

The engine currently splits per-feature workflow state across two files in
the same directory:

- **`state.yaml`** — the durable record: `workflow_plan[phase]` as
  `{active:[ids], filtered:[...], verify:{...}}`, plus `step_history`,
  `next_step`, flags.
- **`plan.yaml`** — a derived artifact written once at init by
  `generate_plan.py` (`_build_step_block`, generate_plan.py:257–304; the
  phase loop at 337–384). It carries per-step `id, agent, goal, inputs,
  outputs, rules, repeat_until, verify`. `dispatch.py` re-loads it on every
  call (`_load_plan` / `_find_step_in_plan`, dispatch.py:214–248) only to
  attach `step_context` to the action.

Holding the same per-step data in two files means two shapes to keep in sync,
two readers, and a `next_step` pointer maintained separately from the plan.
`_resolve_inputs` (dispatch.py:94–128) already computes `(resolved, missing)`
for a step's declared `inputs:`, but `missing` is discarded (dispatch.py:287,
361) — a deliberate M1 no-op (dispatch.py:364–368). A step can be dispatched
with required upstream data absent; the failure surfaces deep inside the
spawned agent.

`record.py` has a second linear scanner, `_compute_next_step`
(record.py:1093–1137), that writes `state.next_step`, and `_detect_boundary`
(record.py:163–164) indexes `active[-1]`. It also already validates declared
outputs at the record boundary (record.py:1189–1199) — but only checks
dict-key presence, not that the value is real.

Several step contracts carry prose in `inputs:`/`outputs:` (e.g.
`design-and-draft-artifacts.yaml`:
`inputs: ['phase_context_bundle (includes discovery_result)', ...]`), which
`parser._load_contract` coerces to strings (parser.py:130–137) — unusable for
a reliable prerequisite match. `phase_context_bundle` is declared as an input
by six contracts (`diagnose`, `explore`, `design-and-draft-artifacts`,
`execute-next-task`, `ux-design`, `run-phase-review`) yet produced by no step
and stored nowhere.

This ticket makes step ordering an explicit dependency graph, **folds
`plan.yaml` into `state.yaml`** so each phase carries a single `nodes` list
with per-node status, promotes the prerequisite check from no-op to hard
block, prunes prose/phantom inputs so the check matches real dataflow, and
adds two read-only CLI subcommands for graph visibility. It is the foundation
for parallel dispatch and meta-steps (separate tickets).

## Goals / Non-Goals

### Goals

- Promote `workflow_plan[phase]` in `state.yaml` from
  `{active:[ids], filtered, verify}` to
  `{nodes:[{id, depends_on, status, agent, goal, inputs, outputs, rules,
  repeat_until}], filtered, verify}` — the shape the ticket specified. Each
  node carries a `status` from the same vocabulary as `step_history.status`.
- Eliminate `plan.yaml`: `generate_plan.py` writes the enriched `nodes` list
  directly into `state.yaml` at init; `dispatch.py` reads node data from
  `state.workflow_plan`. There is no second file.
- Make step ordering an explicit DAG: each node may carry
  `depends_on: [id, ...]`. Absent ⇒ implicit chain edge on the declaration-
  order predecessor.
- Replace the linear next-step scan in `dispatch.py` with a DAG walk that
  selects the first not-completed node whose `depends_on` entries are all
  completed, with a deterministic declaration-order tiebreak.
- Promote the `_resolve_inputs` `missing` list from no-op to a hard dispatch
  block (exit 2) for required inputs; optional inputs never block.
- Prune `phase_context_bundle` and prose qualifiers from contract
  `inputs:`/`outputs:` so every declared item is a bare identifier naming a
  real dataflow edge (an upstream step's output) or a real path.
- Detect dependency cycles at node-generation time via topological sort, so
  dispatch never sees a cyclic graph.
- Add `orchestrator ready` (JSON array of all ready node ids) and
  `orchestrator graph` (Mermaid render of the phase DAG).
- Enforce declared `outputs:` as a contract at the record boundary: a step
  that returns completion without verifiable declared outputs fails.

### Non-Goals

- Parallel or concurrent dispatch — `orchestrator next` stays single-node
  (returns the first ready node). Separate ticket (ORC-64/65).
- Runtime DAG expansion / meta-step nodes — separate ticket.
- Dataflow inference of edges — `depends_on` edges are authored explicitly in
  schema step entries; the engine never derives an edge from
  `inputs:`/`outputs:`.
- Edge authoring in step contracts (`config/steps/*.yaml`) — `depends_on`
  lives in workflow schema `steps:` entries to keep contracts portable.
- New workflow schemas — only existing schemas gain implicit chain edges.
- Changing the `orchestrator next` / `orchestrator done` driver interface —
  `skills/orchestrate/SKILL.md` and `skills/developer/SKILL.md` are unchanged
  except for documentation touches noted in Components.
- UI — no UI components.

## Approaches Considered

### Approach 1: keep plan.yaml; depends_on on plan.yaml nodes

`generate_plan.py` threads `depends_on` into `plan.yaml` nodes; `state.yaml`
keeps `active:[ids]` unchanged.

- **Pros**: smallest diff; no `state.yaml` migration.
- **Cons**: keeps two files holding per-step data; `next_step` stays a
  separate pointer; node status has no home; deviates from the ticket's
  explicit `{nodes:[{id,depends_on}],verify}` model.
- **Complexity**: M

### Approach 2: fold plan.yaml into state.yaml; nodes with per-node status

`workflow_plan[phase]` in `state.yaml` promotes to
`{nodes:[{...}], filtered, verify}`. Each node carries the per-step data
plan.yaml held today plus a `status` field. `generate_plan.py` writes the
`nodes` list into `state.yaml` at init (its single invocation point);
`plan.yaml` is eliminated. `dispatch.py` reads node data from
`state.workflow_plan`; `_load_plan` / `_find_step_in_plan` are deleted.
`record.py` and `dispatch.py` mutate `node.status` through one shared helper.
`state.next_step` is kept as a denormalized convenience pointer derived from
node status. A shared `readiness.py` module is the single DAG walker.

- **Pros**: one file, one shape; `node.status` is the source of truth for
  "what's next"; `dispatch.py` stops re-reading a second file every call;
  matches the ticket's stated model exactly; simplifies state updates
  (record.py writes one place).
- **Cons**: larger diff — `record._detect_boundary` and `_compute_next_step`
  move off `active[-1]`; ~14 test files migrate from `plan.yaml` fixtures to
  `state.yaml` fixtures; `seed-state.sh` post-check changes.
- **Complexity**: L

### Approach 3: dependency edges inferred from inputs:/outputs:

Derive edges automatically from `outputs:`/`inputs:` intersection.

- **Pros**: no edge authoring.
- **Cons**: explicitly out of scope ("NOT using dataflow inference").
- **Complexity**: M (rejected on scope)

### Selected Approach

**Approach 2.** The ticket text specifies the target model directly —
"`workflow_plan[phase]` promotes from `{active:[ids]}` to
`{nodes:[{id,depends_on}],verify}`" — and the change owner has explicitly
chosen to fold `plan.yaml` into `state.yaml` in this ticket so that workflow
state lives in one file with one shape, and per-node status replaces a
separately-maintained `next_step` scan. Approach 1 keeps two files and a
separate pointer for a smaller diff, but the duplication is exactly the
maintenance cost the owner is paying down. Approach 3 is an explicit
non-goal. The larger diff of Approach 2 is the point: `_detect_boundary` and
`_compute_next_step` get simpler, not just different, once they read one
`nodes` list. The migration cost is bounded — `generate_plan.py` re-derives
the `nodes` list from schema on its single init invocation, so in-flight
workflows need no migration script (see Decisions OQ-6).

## High-Level Design

### Architecture Overview

```
 schema (feature.yaml: steps:[ {id, depends_on?} | "id" ])
        │
        ▼
 seed-state.sh ── seeds workflow_plan{main:{active,filtered}} ──► state.yaml
        │
        ▼
 generate_plan.py ── topo-sort cycle check (UC-E1) ──────────────┐
        │            promotes active:[ids] → nodes:[{...}]       │
        │            in-place inside state.yaml workflow_plan    │
        ▼                                                        │
 state.yaml  workflow_plan[phase] = {nodes:[...], filtered, verify}
        │            each node: id, depends_on, status,
        │            agent, goal, inputs, outputs, rules, repeat_until
        │
        ├──► graph.py ──► orchestrator graph  (Mermaid)
        │
        ▼
 dispatch.py ── readiness.next_ready_node() ──► orchestrator next  (first ready)
        │            │                          orchestrator ready (all ready)
        │            │
        │            ├─ marks chosen node.status = in_progress (shared helper)
        │            ▼
        │      prereq check (_resolve_inputs missing → exit 2)
        ▼
 agent spawn / inline run
        │
        ▼
 record.py ── marks node.status = completed (shared helper)
        │            re-derives state.next_step from node status
        ▼
 record.py ── output post-check (declared outputs verifiably present)
```

There is one workflow-state file. `generate_plan.py` runs once at init and
enriches `state.yaml`'s `workflow_plan` in place. After init, only
`dispatch.py` (→ `in_progress`) and `record.py` (→ `completed` / `skipped`)
mutate node status, both through the same helper.

### Key Abstractions

- **Plan node**: an entry in `workflow_plan[phase].nodes`. Fields:
  `id` (str), `depends_on` (list[str], optional — absent ⇒ implicit chain
  edge on the declaration-order predecessor; the first node of a phase has no
  implicit edge), `status` (enum, see below), and the per-step data formerly
  in `plan.yaml`: `agent`, `goal`, `inputs`, `outputs`, `rules`,
  `repeat_until` (when set). Phase-level `filtered` and `verify` remain
  siblings of `nodes`.
- **Node status enum**: `pending | in_progress | completed | skipped` — the
  same vocabulary as `step_history.status` (no translation layer). A node is
  born `pending`; `dispatch.py` sets `in_progress` on spawn; `record.py` sets
  `completed` on done, or `skipped` for a filtered/no-op node. `node.status`
  is the **source of truth** for "what's next".
- **Readiness**: a node is *ready* when it is not `completed` and every entry
  in its effective `depends_on` is `completed` for the current phase. For a
  `repeat_until` node, a dependent treats it as a completed dependency only
  when its status is `completed` AND its `repeat_until` predicate evaluates
  True (see Decisions OQ-5).
- **`next_ready_node(state) → str | None`**: the single DAG-walk function in
  `readiness.py`. Returns the first ready node id in declaration order, or
  `None` when the phase is complete. Used by `dispatch.py` (next/ready),
  `record.py` (`next_step` derivation), and `graph.py`.
- **`state.next_step`**: a denormalized convenience pointer
  (`{phase, step_id}`) kept for the resume mechanism (`resume-token.md`,
  `seed-state.sh`, `skills/orchestrate/SKILL.md`). It is *derived* —
  `record.py` rewrites it to `next_ready_node(state)` after every
  completion. It is never read for dispatch decisions; node status is.
- **Required vs optional input**: `StepContract` gains
  `optional_inputs: list[str]`. An input in `optional_inputs` never blocks
  dispatch; any other declared input is required.
- **Declared `inputs:` are dataflow edges only**: a contract's `inputs:`
  enumerates data the step depends on a *prior step having produced* (an
  upstream `outputs:` name) or a real artifact path. Engine-provided ambient
  context (`change_id`, `repo_root`, `phase`, `step_id`, artifact dirs) is
  supplied to every step via env and is NOT declared as an input. This is the
  rule that retires `phase_context_bundle` (see Decisions OQ-2).

## Low-Level Design

### Components

1. **`state.yaml` `workflow_plan` shape** — `workflow_plan[phase]` becomes
   `{nodes:[...], filtered:[...], verify:{...}}`. `seed-state.sh` still seeds
   a minimal `{main:{active:[ids], filtered:[...]}}`; `generate_plan.py`
   promotes `active` to `nodes` (see Component 4). A parser helper
   `phase_nodes(state, phase) → list[dict]` reads
   `workflow_plan[phase].nodes`, accepting a legacy `active:[ids]` block by
   synthesizing bare `pending` nodes (back-compat read path, see OQ-6).

2. **`readiness.py`** (new module, `config/scripts/orchestrator_next/`).
   Pure functions over `state`:
   - `effective_depends_on(nodes, node_id) → list[str]` — authored
     `depends_on`, else the implicit single-element chain edge, else `[]`.
   - `is_node_ready(state, node_id) → bool` — node not `completed` and all
     effective dependencies `completed` (respecting `repeat_until`).
   - `ready_nodes(state) → list[str]` — every ready node in declaration
     order.
   - `next_ready_node(state) → str | None` — `ready_nodes(state)[0]` or
     `None`.
   - `mark_node_status(state_raw, phase, node_id, status) → None` — the one
     mutator; both `dispatch.py` and `record.py` call it so the two status
     writers stay aligned.

3. **`parser.py`** — `StepContract` gains
   `optional_inputs: list[str] = field(default_factory=list)`.
   `_load_contract` parses an optional-input annotation: a list-form
   `inputs:` item written as `{<name>: optional}` contributes `<name>` to
   both `inputs` and `optional_inputs`; bare-string items remain required.
   Contracts with no annotations are unaffected.

4. **`generate_plan.py`** — its single job becomes promoting
   `state.workflow_plan[phase].active` (the minimal list seed-state writes)
   into a `nodes` list written back into `state.yaml`, then deleting the
   `active` key. `_build_step_block` (today's plan.yaml node builder) is
   reused to produce each node's `agent/goal/inputs/outputs/rules/
   repeat_until`; it additionally reads `depends_on` from the schema step
   entry (dict form) and sets `status: pending`. A new `_topo_sort(nodes)`
   builds the effective edge set (authored + implicit chain) and raises a
   clear error naming the cycle if one exists. Edges that target a `filtered`
   step are dropped with a stderr warning (OQ-1). The output target changes
   from `plan.yaml` to an in-place rewrite of `state.yaml`. `generate_plan`
   runs exactly once (verified: only caller is `seed-state.sh:203`), so it
   never races a live node status.

5. **`dispatch.py`** — `_phase_step_ids` and the linear scan
   (dispatch.py:308–323) are replaced by `readiness.next_ready_node(state)`.
   `_load_plan` / `_find_step_in_plan` (dispatch.py:214–248) are deleted;
   `step_context` is built from the chosen node dict in
   `state.workflow_plan`. After resolving the chosen contract's inputs, the
   prerequisite check filters `missing` against `contract.optional_inputs`;
   any remaining *required* names ⇒ return `({}, 2)` with a stderr
   diagnostic naming the missing input(s) and the blocked node. The M1 no-op
   comment (dispatch.py:364–368) is deleted. On a successful agent/inline
   dispatch, the chosen node is marked `in_progress` via
   `readiness.mark_node_status`.

6. **`record.py`** — `_detect_boundary` (record.py:163–164) and
   `_compute_next_step` (record.py:1093–1137) read `nodes` instead of
   `active`: "last node" = last declaration-order node that is not
   `filtered`; "next step" = `next_ready_node(state)`. On a completed record,
   `record.py` calls `readiness.mark_node_status(..., "completed")` and
   rewrites `state.next_step` from `next_ready_node`. The output post-check
   at record.py:1189–1199 is upgraded: a declared output is *satisfied* only
   when its key is in `evidence.outputs`, the value is non-null and
   non-empty, and — for an output whose name is a filesystem path (contains
   `/`, e.g. `spec/project.yaml`) — the file exists on disk (resolved
   relative to the worktree artifact dir / repo root). Failure returns the
   existing `missing_outputs` rejection (exit 3).

7. **Step contracts** — prune `phase_context_bundle` from the `inputs:` of
   all six contracts that declare it, and align every remaining declared
   input with a real producer (verified per-contract in Decisions OQ-2):
   - `design-and-draft-artifacts` → `inputs: [discovery_result]`;
     `outputs:` gains the path-named artifacts it actually writes —
     `outputs: [design.md, tasks.md, updated_artifact_set, design_direction,
     complexity]`. This makes `tasks.md` a declared output with a real
     producer.
   - `execute-next-task` → `inputs: [tasks.md]` — now resolvable, produced by
     `design-and-draft-artifacts`.
   - `run-phase-review` → `inputs: [task_execution_result]` (produced by
     `execute-next-task`).
   - `ux-design` → `inputs: [discovery_result]` (produced by `explore`).
   - `explore` / `diagnose` → `inputs: []` (phase-opening steps).
   - `generate-project-yaml` / `install-tooling` / `run-ux-critique` →
     prose normalized to bare identifiers. Their upstream producers are
     inline shell steps (`detect-language`, `install-tooling`) that emit
     outputs at runtime via stdout JSON, not via a static contract
     `outputs:` declaration — so these required inputs resolve against
     runtime `evidence.outputs` at dispatch, not against contract text (see
     Decisions, prereq resolution rule).
   A regression-guard test asserts no contract `inputs:`/`outputs:` item
   contains `(` or parses as a mapping, no contract declares
   `phase_context_bundle`, and every required input name is either an
   upstream contract `outputs:` entry, a top-level `state.raw` key, or
   produced by an inline step earlier in the phase.

8. **`bin/orchestrator`** — two new verbs:
   - `ready <state.yaml>` — load state, print
     `json.dumps(readiness.ready_nodes(state), sort_keys=True, indent=2)`,
     exit 0.
   - `graph <state.yaml>` — load state, print a Mermaid `flowchart TD` of the
     current phase DAG, each node labelled with its status, exit 0.
     Implemented in a new `graph.py` module.
   Both are read-only — no `state.yaml` write, no DuckDB write. The usage
   banner gains both verbs.

9. **`seed-state.sh`** — the post-`generate_plan` existence check changes
   from "`plan.yaml` exists" (seed-state.sh:211–215) to
   "`state.yaml` `workflow_plan.main.nodes` is a non-empty list". The
   `next_step` seed (seed-state.sh:183) is unchanged in shape. Comments
   referencing `plan.yaml` (header lines 7, 17) are updated.

10. **Contract docs** — `config/steps/contracts/done-payload.md` and
    `config/steps/contracts/resume-token.md` are updated: `plan.yaml` no
    longer exists; `workflow_plan[phase]` is documented as
    `{nodes, filtered, verify}` with per-node `status`; `next_step` is
    documented as a derived convenience pointer (source of truth =
    `node.status`). `skills/orchestrate/SKILL.md` line 98's
    "post generate-plan-yaml-at-init" note is corrected to reflect the
    single-file model.

### Data Flow

1. Schema author writes `steps:` entries; a step needing an explicit edge is
   written in dict form `{id: design-and-draft-artifacts, depends_on:
   [explore]}`. Plain-string entries keep the implicit chain.
2. `seed-state.sh` writes a minimal `state.yaml` with
   `workflow_plan.main = {active:[ids], filtered:[...]}`, then invokes
   `generate_plan.py`.
3. `generate_plan.py` builds each node (per-step data + `depends_on` +
   `status: pending`), topo-sorts (cyclic edges abort here), and rewrites
   `state.yaml` with `workflow_plan.main = {nodes:[...], filtered, verify}`.
4. `orchestrator next` → `dispatch.py` loads `state`, calls
   `next_ready_node`, resolves the chosen contract's inputs, applies the
   required-input prereq check, marks the node `in_progress`, and emits the
   action (exit 0) or blocks (exit 2).
5. `orchestrator ready` / `orchestrator graph` → same load, read-only output.
6. Step completes → `orchestrator done` → `record.py` applies the output
   post-check, appends `step_history`, marks the node `completed`, re-derives
   `state.next_step`.

### State Management

- One file: `state.yaml`. `plan.yaml` no longer exists.
- `workflow_plan[phase] = {nodes:[{id, depends_on?, status, agent, goal,
  inputs, outputs, rules, repeat_until?}], filtered:[...], verify:{...}}`.
- `node.status` ∈ `{pending, in_progress, completed, skipped}` — source of
  truth for dispatch readiness.
- `state.next_step` is derived (`record.py` rewrites it after every
  completion); kept only for the resume mechanism.
- `step_history` is unchanged; `_compute_attempt` still derives attempt
  counts from `step_history`, never from `node.status`.

### Error Handling

- **Cycle in schema edges** → `generate_plan.py` raises with the cycle path;
  `state.yaml` is not promoted (the partial state is removed by
  `seed-state.sh`'s existing failure handling); the workflow cannot start.
  (UC-E1)
- **`depends_on` targets a filtered step** → `generate_plan.py` drops the
  edge with a stderr warning naming it; node generation continues. (UC-E2,
  Decisions OQ-1)
- **Required input unresolvable at dispatch** → `dispatch.py` returns exit 2
  with a stderr diagnostic: the missing input name, the blocked node, and the
  hint that an upstream producer has not completed. (UC-E3)
- **Optional input absent** → no block; dispatch proceeds. (UC-6)
- **Declared output missing/empty at record** → `record.py` returns exit 3
  with `missing_outputs`; the step is not recorded `completed`. (UC-7)
- **`depends_on` references an unknown step id** → `generate_plan.py`
  topo-sort treats it as an unsatisfiable edge and raises at node-generation
  time.
- **Legacy `active:[ids]` block read at runtime** → the parser back-compat
  read path synthesizes bare `pending` nodes so a state.yaml that predates
  promotion still dispatches (UC-E4, Decisions OQ-6).

## Constraints

- Standard library + `pyyaml` + `duckdb` only. Topo-sort is a ~15-line
  stdlib implementation (Kahn's algorithm); no external DAG library.
- Schemas and step contracts must not name any specific LLM tool.
- `state.yaml` writes stay stable/diffable (`yaml.safe_dump`,
  `sort_keys=False`, caller-controlled key order — the existing
  `_write_yaml_stable` / record.py write style).
- Performance: `orchestrator next` must stay well under its production
  budget of ~1 s wall time; the DAG walk is O(nodes × edges) on a phase of
  at most ~15 nodes — negligible, and it now reads one file instead of two.

## Trade-offs

- **`plan.yaml` is eliminated; `state.yaml` grows.** Accepted: one file with
  one shape removes the sync burden between two artifacts and lets
  `record.py` update node status in the same write it already does. The cost
  — migrating ~14 test files from `plan.yaml` fixtures to `state.yaml`
  fixtures — is mechanical and bounded.
- **`record._detect_boundary` / `_compute_next_step` change.** Accepted: they
  get *simpler* — both read one `nodes` list with explicit status instead of
  indexing `active[-1]` and re-deriving completion from `step_history`.
- **`state.next_step` is kept as a derived field, not deleted.** Accepted:
  `resume-token.md` is an external contract, `seed-state.sh` writes it at
  init, and `skills/orchestrate/SKILL.md` reads it on resume. Deleting it
  would touch two external contracts, a skill, and a script for no
  functional gain — node status already drives dispatch. Keeping it derived
  costs one `next_ready_node` call per `record`.
- **`phase_context_bundle` is removed from contracts, not redefined.**
  Accepted: it named no real dataflow edge — it was prose for "the context
  the engine supplies." Engine-provided ambient context is delivered via env
  to every step and needs no declaration. Pruning it makes the prereq check
  need no special-case skip: every remaining declared input is a real edge.
- **Two writers to `node.status`** (`dispatch.py` → `in_progress`,
  `record.py` → `completed`). Accepted: this mirrors `step_history` exactly
  and both go through the single `readiness.mark_node_status` helper.

## Acceptance Criteria

- AC-1: Given an existing linear schema with no `depends_on` entries, when
  `generate_plan.py` promotes `state.yaml`, then `workflow_plan[phase].nodes`
  contains one node per step in declaration order, each with
  `status: pending` and an implicit chain edge on its predecessor, and
  `dispatch.py` dispatches them in the same order as the pre-ORC-63 linear
  scan. [traces: UC-1]
- AC-2: Given a schema step entry with explicit `depends_on: [explore]`, when
  `generate_plan.py` runs, then the corresponding node carries
  `depends_on: [explore]`, and `dispatch.py` does not select that node until
  the `explore` node's status is `completed`. [traces: UC-2]
- AC-3: Given a phase with several nodes, when `dispatch.py` walks the DAG,
  then it selects the first not-completed node (declaration order) whose
  `depends_on` entries are all completed; ties are broken by declaration
  order deterministically. [traces: UC-1, UC-2]
- AC-4: Given a step contract declaring a required input that is not present
  in any prior `completed` step's `evidence.outputs` and not in `state.raw`,
  when `orchestrator next` runs, then it exits 2 with a stderr diagnostic
  naming the missing input and the blocked node. [traces: UC-3, UC-E3]
- AC-5: Given a step contract whose `inputs:` declares a name annotated
  optional, when that input is absent from step history, then `dispatch.py`
  does not block and proceeds. [traces: UC-6]
- AC-6: Given the step contracts that currently declare `phase_context_bundle`
  or carry prose qualifiers (`diagnose`, `explore`,
  `design-and-draft-artifacts`, `execute-next-task`, `ux-design`,
  `run-phase-review`, `generate-project-yaml`, `install-tooling`,
  `run-ux-critique`), when they are pruned and normalized, then every
  `inputs:`/`outputs:` item is a bare identifier (no `phase_context_bundle`,
  no parentheses, no mapping); every required input resolves to an upstream
  contract `outputs:` entry, a `state.raw` key, or an earlier inline step's
  runtime output; `design-and-draft-artifacts.outputs` includes the
  path-named `tasks.md` so `execute-next-task.inputs: [tasks.md]` has a
  producer; and a regression test asserts all of this across all contracts.
  [traces: UC-3]
- AC-7: Given a workflow schema whose `depends_on` edges form a cycle, when
  `generate_plan.py` runs, then topo-sort detects the cycle and the command
  exits non-zero with a stderr message naming the cycle path, and
  `state.yaml` is not promoted to the `nodes` shape. [traces: UC-E1]
- AC-8: Given a valid `state.yaml`, when `orchestrator ready <state.yaml>`
  runs, then it prints a JSON array of all currently-ready node ids and exits
  0; `orchestrator next` returns the action for the first ready node and the
  `orchestrator next` / `orchestrator done` driver interface is unchanged.
  [traces: UC-4]
- AC-9: Given a valid `state.yaml`, when `orchestrator graph <state.yaml>`
  runs, then it prints a Mermaid `flowchart TD` of the current phase DAG with
  each node labelled by status and exits 0. [traces: UC-5]
- AC-10: Given an agent step that declares `outputs: [discovery_result]` and
  returns a `done` payload whose `outputs` lacks `discovery_result`, or whose
  value is null/empty, or (for a path-named output) whose file does not
  exist, when `orchestrator done` runs, then `record.py` rejects the payload
  with a `missing_outputs` error (exit 3) and the step is not recorded
  `completed`. [traces: UC-7]
- AC-11: Given an in-flight `state.yaml` that still carries the legacy
  `workflow_plan[phase].active:[ids]` shape, when `orchestrator next` runs,
  then the parser back-compat read path treats each id as a `pending` node
  and dispatch proceeds without error or migration script. [traces: UC-E4]

## Decisions

- **OQ-1 (`depends_on` targets a filtered step)** → `generate_plan.py` drops
  the edge to the filtered step and emits a stderr warning naming it →
  Hard-erroring would force every schema author to write conditional
  `depends_on` per optional-step flag (`ux_design`, `merge_to_main`);
  silently treating it as satisfied is semantically wrong. Drop-with-warning
  matches the `filtered:[...]` semantics where a removed step ceases to exist
  in the phase. (Approved unchanged from the prior revision.)
- **OQ-2 (`phase_context_bundle`)** → `phase_context_bundle` is **removed
  from all six contract `inputs:` lists**; a contract's `inputs:` enumerates
  real dataflow edges only → Verified per contract: `design-and-draft-
  artifacts` truly depends on `discovery_result` (the prose said so:
  "includes discovery_result"); `run-phase-review` depends on
  `task_execution_result`; `execute-next-task` depends on the `tasks.md`
  artifact; `ux-design` depends on `discovery_result`; `explore` and
  `diagnose` open a phase and have no upstream edge (`inputs: []`).
  `phase_context_bundle` named no producer and no stored value — it was prose
  for engine-supplied ambient context, which every step already receives via
  env. Pruning it (rather than inventing a producer step or a sentinel skip
  rule) makes the prereq check need no special case: every remaining declared
  input is a real edge the check can verify.
- **OQ-3 (output post-check location)** → The output post-check lives in
  `record.py`, upgrading the existing check at record.py:1189–1199 → The hook
  already exists at that exact site (currently dict-key presence only); AC-10
  strengthens that check rather than adding a new boundary. Earliest
  feedback, no done-payload contract change.
- **Prereq resolution rule (AC-4)** → A required input is *missing* iff
  **(a)** it is not the key of any prior `completed` step's
  `evidence.outputs`, AND **(b)** it is not a top-level key of `state.raw`.
  No special cases, no sentinel names → The check is purely runtime: it reads
  `evidence.outputs` from `step_history`, so an input produced by an inline
  shell step (which emits outputs via stdout JSON, not a static contract
  `outputs:`) resolves correctly once that step has completed. Contract
  `outputs:` declarations are validated separately at the *producer* by AC-10;
  the prereq check at the *consumer* trusts recorded runtime evidence. Stating
  the rule with no exceptions closes off any future "add a sentinel for X"
  question.
- **OQ-4 (`state.yaml` shape for `depends_on`)** → `plan.yaml` is
  **eliminated**; `workflow_plan[phase]` in `state.yaml` promotes to
  `{nodes:[{id, depends_on, status, ...}], filtered, verify}` and carries the
  graph, per-node status, and all per-step data formerly in `plan.yaml` →
  This is the model the ticket text specified. One file, one shape; node
  status is the source of truth for dispatch readiness; `record.py` updates
  status in the same write it already performs. (Revised from the prior
  Approach-1 resolution at the change owner's direction.)
- **OQ-5 (`repeat_until` interaction with DAG readiness)** → A `repeat_until`
  node counts as a *completed dependency* for its dependents only when its
  status is `completed` AND its `repeat_until` predicate evaluates True →
  This promotes the condition already special-cased at dispatch.py:319–323
  into the readiness predicate. A downstream `depends_on: [execute-next-task]`
  node thus waits until all tasks are genuinely done.
- **OQ-6 (in-flight workflow migration)** → No migration script → Verified
  `generate_plan.py`'s only caller is `seed-state.sh:203` (init). For an
  in-flight workflow whose `state.yaml` still has `active:[ids]`, the parser
  back-compat read path synthesizes bare `pending` nodes on read, so dispatch
  works unchanged; the next full workflow init produces the `nodes` shape
  natively. No state.yaml is rewritten in place for an in-flight workflow.
- **`state.next_step` retention** → Kept as a derived convenience pointer,
  not deleted → `resume-token.md` is an external contract, `seed-state.sh`
  writes it at init, `skills/orchestrate/SKILL.md` reads it on resume.
  `node.status` is the source of truth; `next_step` is a cached pointer
  `record.py` rewrites from `next_ready_node` after every completion.
- **Node status vocabulary** → `pending | in_progress | completed | skipped`,
  identical to `step_history.status` → One vocabulary across `step_history`
  and `node.status` avoids a translation layer. YAML-native enum, not
  markdown checklist markers (markers belong to the `tasks.md` artifact, not
  to structured state).
- **Shared readiness/mutation helper** → A single `readiness.py` module is
  the one DAG walker and the one `node.status` mutator → `dispatch.py` and
  `record.py` both depend on it, so the two status writers and the next-node
  computation cannot drift.

## Open Questions

- None — OQ-1 through OQ-6 are resolved above.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
