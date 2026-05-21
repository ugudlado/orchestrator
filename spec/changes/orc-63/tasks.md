# Tasks — DAG Dispatch Foundation

## Group A — node shape + readiness foundation

- [x] T-1: Write tests for parser node-shape read path (RED)
  Why: AC-1, AC-11 — workflow_plan must expose a `nodes` list and still read a legacy `active:[ids]` block
  Files: config/scripts/orchestrator_next/tests/test_parser.py
  Change: Add cases for a not-yet-existing parser helper `phase_nodes(state, phase)` — a `workflow_plan.main.nodes:[{id,status,...}]` block returns verbatim; a legacy `active:[ids]` block returns synthesized nodes each `{id, status:'pending'}`; a `{nodes:[...]}` block is returned unchanged. Tests fail today because `phase_nodes` does not exist.
  Test scenarios:
    - a `workflow_plan.main.nodes` block is returned verbatim
    - a legacy `active:[ids]` block yields synthesized `{id, status:'pending'}` nodes, one per id
    - a `{nodes:[...]}` block is returned unchanged regardless of node count
    - tests fail for the right reason today (AttributeError/ImportError — `phase_nodes` absent, not a typo)

- [x] T-2: Implement parser node-shape read path (GREEN)
  Why: AC-1, AC-11 — single read path over the new `nodes` shape with back-compat for in-flight workflows (design.md OQ-6)
  Files: config/scripts/orchestrator_next/parser.py
  Change: Add `phase_nodes(state, phase) -> list[dict]` reading `state.workflow_plan[phase].nodes`; when that key is absent but `active:[ids]` is present, synthesize `[{id: x, status: 'pending'} for x in active]`. Pure read — no state mutation.
  Test scenarios:
    - all T-1 cases pass
    - existing test_parser.py stays green
    - type-check clean

- [x] T-3: Write tests for parser optional-input annotation (RED)
  Why: AC-5 — contracts must distinguish optional inputs from required ones
  Files: config/scripts/orchestrator_next/tests/test_parser.py
  Change: Add cases for the not-yet-existing `StepContract.optional_inputs` field and its parsing. Tests fail today because `StepContract` has no `optional_inputs` field.
  Test scenarios:
    - a contract `inputs:` item written `{<name>: optional}` yields `<name>` in both `StepContract.inputs` and `StepContract.optional_inputs`
    - a bare-string `inputs:` item yields the name in `inputs` only (not in `optional_inputs`)
    - a contract with no annotated items yields `optional_inputs == []`
    - tests fail for the right reason today (field absent)

- [x] T-4: Implement parser optional-input parsing (GREEN)
  Why: AC-5 — an optional input must never block dispatch
  Files: config/scripts/orchestrator_next/parser.py
  Change: Add `optional_inputs: list[str] = field(default_factory=list)` to the `StepContract` dataclass (parser.py:31-47). In `_load_contract` (parser.py:101-155), at the input coercion step (parser.py:134-137), detect a single-key mapping item `{name: 'optional'}`: append `name` to both `inputs` and `optional_inputs`; bare strings append to `inputs` only.
  Test scenarios:
    - all T-3 cases pass
    - existing test_parser.py stays green
    - type-check clean
  depends: T-3

- [x] T-5: Write tests for readiness.py (RED)
  Why: AC-2, AC-3, AC-5, OQ-5 — the shared DAG-walk module is the single source of node readiness
  Files: config/scripts/orchestrator_next/tests/test_readiness.py
  Change: New test file for the not-yet-existing `readiness` module. Tests fail today because the module does not exist.
  Test scenarios:
    - `effective_depends_on` synthesizes an implicit chain edge (predecessor id) for a node with no `depends_on`
    - `effective_depends_on` returns `[]` for the first node of a phase
    - explicit `depends_on` is honored verbatim
    - `is_node_ready` is True only when every effective dependency has `status=='completed'`
    - a `repeat_until` dependency counts as completed only when `status=='completed'` AND its predicate returns True
    - `ready_nodes` returns ready not-completed nodes in declaration order
    - `next_ready_node` returns `ready_nodes[0]` or None
    - `mark_node_status` flips one named node's `status` in `workflow_plan`
    - tests fail today (ModuleNotFoundError)

- [x] T-6: Implement readiness.py (GREEN)
  Why: AC-2, AC-3, AC-5, OQ-5 — one DAG walker + one status mutator shared by dispatch and record (prevents drift)
  Files: config/scripts/orchestrator_next/readiness.py
  Change: New module with pure functions over the parsed state: `effective_depends_on(nodes, node_id)`, `is_node_ready(state, node_id)`, `ready_nodes(state)`, `next_ready_node(state)`, and the mutator `mark_node_status(state_raw, phase, node_id, status)`. Read nodes via `parser.phase_nodes`. For the `repeat_until` check, reuse `REPEAT_PREDICATES` from record.py (mirrors the existing dispatch.py:319-323 special-case).
  Test scenarios:
    - all T-5 cases pass
    - type-check clean
  depends: T-2, T-4, T-5

- [x] T-7: Review checkpoint — parser + readiness foundation (gate)
  Why: phase gate — lock the foundation before generate_plan/dispatch build on it
  Test scenarios:
    - type-check clean
    - full pytest suite green
  depends: T-6

## Group B — generate_plan: fold plan.yaml into state.yaml

- [ ] T-8: Write tests for generate_plan node promotion + topo-sort cycle detection (RED)
  Why: AC-1, AC-2, AC-7, OQ-1, OQ-4 — generate_plan must write the `nodes` shape into state.yaml and reject cycles at plan time
  Files: config/scripts/orchestrator_next/tests/test_generate_plan.py
  Change: Add cases for node promotion and topo-sort. Tests fail today because generate_plan still writes plan.yaml.
  Test scenarios:
    - after `generate_plan`, `state.yaml` `workflow_plan.main` is `{nodes:[{id,status:'pending',agent,goal,inputs,outputs,rules,depends_on?}], filtered, verify}` and NO plan.yaml exists on disk
    - a linear schema yields one node per step in the old `active` order, each with an implicit-chain `depends_on`
    - an explicit `depends_on` on a dict-form schema step entry lands on its node
    - cyclic edges raise non-zero with the cycle path; `state.yaml` keeps its pre-promotion shape
    - a `depends_on` to a `filtered` step is dropped with a stderr warning
    - a `depends_on` to an unknown id raises
    - tests fail for the right reason today

- [ ] T-9: Implement generate_plan node promotion + _topo_sort (GREEN)
  Why: AC-1, AC-2, AC-7, OQ-1, OQ-4 — eliminate plan.yaml; one file carries the graph
  Files: config/scripts/orchestrator_next/generate_plan.py
  Change: Extend `_build_step_block` (generate_plan.py:257-304) to read `depends_on` from the dict-form schema step entry and set `status: 'pending'`. Add `_topo_sort(nodes)` (Kahn's algorithm) over the effective edge set (authored + implicit chain), raising `ValueError` naming the cycle on failure, and dropping edges targeting a `filtered` step with a stderr warning. In the phase loop (generate_plan.py:337-384), call `_topo_sort` before promotion. Change the output target: rewrite `state.yaml` in place with `workflow_plan[phase] = {nodes:[...], filtered, verify}` and delete the `active` key — write no plan.yaml.
  Test scenarios:
    - all T-8 cases pass
    - existing test_generate_plan.py updated and green
    - type-check clean
  depends: T-6, T-8

- [ ] T-10: Update seed-state.sh post-generate_plan check (GREEN)
  Why: OQ-4 — seed-state must validate the new `nodes` shape, not a now-nonexistent plan.yaml
  Files: skills/orchestrate/scripts/seed-state.sh
  Change: Replace the `plan.yaml` existence check (seed-state.sh:211-215) with a check that the post-`generate_plan` (seed-state.sh:203) `state.yaml` has a non-empty `workflow_plan.main.nodes` list. Update the header comments referencing plan.yaml (lines 7, 17). Remove every remaining `plan.yaml` reference from the script.
  Test scenarios:
    - test_seed_state.py is green
    - grep confirms no `plan.yaml` reference remains in seed-state.sh
  depends: T-9

- [ ] T-11: Review checkpoint — single-file workflow state (gate)
  Why: phase gate — confirm plan.yaml is gone before dispatch/record read the new shape
  Test scenarios:
    - type-check clean
    - full pytest suite green
    - a manual seed-state + generate_plan run produces a state.yaml with the nodes shape and no plan.yaml on disk
  depends: T-10

## Group C — dispatch: DAG walk + prereq hard block

- [ ] T-12: Write tests for dispatch DAG-walk + node in_progress write + prereq hard block (RED)
  Why: AC-3, AC-4, AC-5 — dispatch must DAG-walk and hard-block on missing required inputs
  Files: config/scripts/orchestrator_next/tests/test_dispatch.py
  Change: Add cases for node-based selection and the prereq block. Tests fail today because dispatch does a linear `active` scan and discards the `_resolve_inputs` missing list.
  Test scenarios:
    - dispatch selects the first ready node from `workflow_plan.nodes` without loading any plan.yaml
    - the tiebreak is declaration order, deterministic
    - a node with an unmet `depends_on` is skipped
    - the chosen node's `status` becomes `in_progress`
    - `step_context` is built from the node dict
    - a required input absent from every prior `completed` step's `evidence.outputs` and from `state.raw` makes `orchestrator next` exit 2 with a stderr diagnostic naming the input and the blocked node
    - an absent optional input does NOT block
    - tests fail for the right reason today

- [ ] T-13: Implement dispatch DAG-walk + prereq hard block (GREEN)
  Why: AC-3, AC-4, AC-5 — replace the linear scan with DAG readiness and turn the M2 missing-input no-op into a hard block
  Files: config/scripts/orchestrator_next/dispatch.py
  Change: Delete `_phase_step_ids` (dispatch.py:72-77) and the linear selection loop (dispatch.py:308-323); select the next step via `readiness.next_ready_node(state)`. Delete `_load_plan` / `_find_step_in_plan` (dispatch.py:214-248) and build `step_context` from the chosen node dict in `state.workflow_plan`. After `_resolve_inputs` (dispatch.py:94-128), filter the `missing` list against `contract.optional_inputs`; if any required names remain, return `({}, 2)` with a stderr diagnostic naming the input(s) and the node. Delete the M1 no-op comment (dispatch.py:364-368). On a successful agent/inline dispatch, mark the chosen node `in_progress` via `readiness.mark_node_status`.
  Test scenarios:
    - all T-12 cases pass
    - existing test_dispatch*.py (allowed_tools, missing_contract, no_path3, pending_row, resume, step_context, phase_hint) migrated to the nodes shape and green
    - type-check clean
  depends: T-9, T-12

- [ ] T-14: Review checkpoint — DAG dispatch + prereq enforcement (gate)
  Why: phase gate — confirm DAG dispatch and the exit-2 block before record changes
  Test scenarios:
    - type-check clean
    - full pytest suite green
  depends: T-13

## Group D — record: node.status writer + next_step + output post-check

- [ ] T-15: Write tests for record node-aware boundary + next_step derivation + output post-check (RED)
  Why: AC-10 — record must enforce declared outputs and keep node.status / next_step consistent
  Files: config/scripts/orchestrator_next/tests/test_record_validation.py, config/scripts/orchestrator_next/tests/test_boundary_detection.py
  Change: Add cases for the node-aware boundary and the upgraded output post-check. Tests fail today because the boundary indexes `active[-1]` and the output check (record.py:1189-1199) only checks dict-key presence.
  Test scenarios:
    - `_detect_boundary` identifies the last non-filtered node from `workflow_plan.nodes` (not `active[-1]`)
    - a `completed` record flips that node's `status` to `completed` and rewrites `state.next_step` to `next_ready_node`
    - the output post-check rejects a payload whose declared output key is absent from `evidence.outputs`
    - the output post-check rejects a payload whose declared output value is null or empty
    - the output post-check rejects a payload whose path-named output (name contains `/`) file is absent on disk
    - a payload with all declared outputs present, non-empty, and path-files existing is accepted
    - tests fail for the right reason today

- [ ] T-16: Implement record node.status writer + next_step derivation + output post-check upgrade (GREEN)
  Why: AC-10, OQ-3 — enforce outputs at the record boundary; keep next_step derived from node status
  Files: config/scripts/orchestrator_next/record.py
  Change: In `_detect_boundary` (record.py:163-164) and `_compute_next_step` (record.py:1093-1137), read `workflow_plan.nodes` via `parser.phase_nodes`: "last node" = last declaration-order node not in `filtered`; "next step" = `readiness.next_ready_node(state)`. On a `completed` record, call `readiness.mark_node_status(state_raw, phase, step_id, 'completed')` and set `state.next_step` from `next_ready_node`. Upgrade the output check (record.py:1189-1199): a declared output is satisfied only when its key is in `evidence.outputs`, the value is non-null and non-empty, and — if the name contains `/` — the file exists (resolved against the worktree artifact dir / repo root). Failure returns the existing `missing_outputs` shape (exit 3).
  Test scenarios:
    - all T-15 cases pass
    - existing test_record_validation / test_boundary_detection / test_repeat_until / test_orchestrator_record green
    - type-check clean
  depends: T-6, T-13, T-15

- [ ] T-17: Review checkpoint — record enforcement + node status writes (gate)
  Why: phase gate — confirm output enforcement and node-status writes before contract changes
  Test scenarios:
    - type-check clean
    - full pytest suite green
  depends: T-16

## Group E — contract input pruning + normalization (mechanical)

- [ ] T-18: Write regression-guard test for contract inputs/outputs hygiene + producer/consumer integrity (no RED — mechanical change)
  Why: AC-6, OQ-2 — retire phase_context_bundle and guarantee every required input has a real producer
  Files: config/scripts/orchestrator_next/tests/test_prose_contracts.py
  Change: Add a regression-guard case scanning every config/steps/*.yaml. Mechanical change — no behavior delta — so the regression-guard assertion below stands in for a RED test; it fails today against the un-pruned contracts.
  Test scenarios:
    - no `inputs:`/`outputs:` item in any contract contains `(` or parses as a YAML mapping
    - `phase_context_bundle` appears in no contract `inputs:`
    - for the feature schema, every required (non-optional) input name is either an upstream contract `outputs:` entry, a known `state.raw` bootstrap key, or an output of an earlier inline step
    - the case fails today against the current un-pruned contracts

- [ ] T-19: Prune phase_context_bundle + normalize inputs/outputs + add producer outputs (GREEN)
  Why: AC-6, OQ-2 — declared inputs must name real dataflow edges so the AC-4 prereq check matches reliably
  Files: config/steps/design-and-draft-artifacts.yaml, config/steps/explore.yaml, config/steps/diagnose.yaml, config/steps/execute-next-task.yaml, config/steps/ux-design.yaml, config/steps/run-phase-review.yaml, config/steps/generate-project-yaml.yaml, config/steps/install-tooling.yaml, config/steps/run-ux-critique.yaml
  Change: Remove `phase_context_bundle` from every contract `inputs:`. Set `design-and-draft-artifacts` `inputs: [discovery_result]` and extend its `outputs:` to `[design.md, tasks.md, updated_artifact_set, design_direction, complexity]` (so `tasks.md` has a real producer); `execute-next-task` `inputs: [tasks.md]`; `run-phase-review` `inputs: [task_execution_result]`; `ux-design` `inputs: [discovery_result]`; `explore` and `diagnose` `inputs: []`. Rewrite the prose `inputs:`/`outputs:` of `generate-project-yaml`, `install-tooling`, `run-ux-critique` to bare identifier strings.
  Test scenarios:
    - the T-18 regression-guard test passes
    - full pytest suite green
  depends: T-18

## Group F — CLI surface: ready + graph

- [ ] T-20: Write tests for orchestrator ready + graph subcommands (RED)
  Why: AC-8, AC-9 — new read-only verbs expose ready nodes and the DAG
  Files: config/scripts/orchestrator_next/tests/test_cli_ready_graph.py
  Change: New test file for the `ready` and `graph` verbs. Tests fail today because bin/orchestrator rejects `ready`/`graph` and graph.py does not exist.
  Test scenarios:
    - `orchestrator ready <state.yaml>` prints a JSON array of ready node ids and exits 0
    - `orchestrator graph <state.yaml>` prints a Mermaid `flowchart TD` with one entry per node labelled by status and exits 0
    - both verbs leave state.yaml byte-unchanged and write no DuckDB rows
    - `orchestrator next` still returns the first ready node
    - tests fail for the right reason today

- [ ] T-21: Implement orchestrator ready + graph verbs (GREEN)
  Why: AC-8, AC-9 — operator visibility into readiness and DAG shape, with `next` unchanged
  Files: bin/orchestrator, config/scripts/orchestrator_next/graph.py
  Change: Add `ready` and `graph` to the accepted-verbs tuple in `bin/orchestrator` `main` and route them. `ready`: load state, print `json.dumps(readiness.ready_nodes(state), sort_keys=True, indent=2)`, exit 0. `graph`: load state, call new `graph.py` which renders a Mermaid `flowchart TD` from `workflow_plan.nodes` with per-node status labels, print it, exit 0. Both paths are read-only — no state.yaml write, no DuckDB connection. Add both verbs to the usage banner (`_usage`).
  Test scenarios:
    - all T-20 cases pass
    - existing test_dispatch / test_retired_cli green
    - type-check clean
  depends: T-13, T-20

## Group G — external contract docs

- [ ] T-22: Update contract docs for the single-file model (no RED — documentation)
  Why: OQ-4 — external contracts must describe the single-file `nodes` model and the derived next_step
  Files: config/steps/contracts/resume-token.md, config/steps/contracts/done-payload.md, skills/orchestrate/SKILL.md
  Change: In resume-token.md and done-payload.md, describe `workflow_plan[phase]` as `{nodes, filtered, verify}` with per-node `status`, document `next_step` as a derived convenience pointer (source of truth = `node.status`), and remove every reference to plan.yaml. Correct the "post generate-plan-yaml-at-init" note at skills/orchestrate/SKILL.md:98 to reflect the single-file model. Documentation change — the regression-guard below stands in for a RED test.
  Test scenarios:
    - test_prose_contracts.py stays green
    - grep confirms no `plan.yaml` reference remains in resume-token.md, done-payload.md, or skills/orchestrate/SKILL.md
  depends: T-16

## Final gate

- [ ] T-23: Review checkpoint — full feature verification (gate)
  Why: final gate — verify all 11 design.md ACs and that the driver interface is unchanged
  Test scenarios:
    - type-check clean
    - full pytest suite green
    - all 11 design.md ACs covered by a passing test
    - no plan.yaml produced by any code path
    - `orchestrator next` / `orchestrator done` driver interface unchanged (skills/orchestrate + skills/developer untouched except the SKILL.md doc note)
  depends: T-17, T-19, T-21, T-22

<!-- Format contract: contracts/artifact-formats.md § Task Format Contract -->
<!-- Each task carries: Why / Files / Change / Test scenarios / depends. -->
<!-- Test scenarios is a bulleted list of behaviors the tests should cover; -->
<!-- the developer may add more scenarios as needed. -->
<!-- Status markers: [ ] pending, [x] done. -->
<!-- TDD: RED test tasks precede GREEN implementation tasks (carried by depends:). -->
<!-- T-18 (mechanical contract prune) + T-22 (docs) use a regression-guard -->
<!-- assertion instead of a fabricated RED, per the learned mechanical-change rule. -->
