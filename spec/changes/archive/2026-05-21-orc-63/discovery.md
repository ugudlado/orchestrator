---
feature-id: orc-63
linear-ticket: N/A
---

# Discovery Brief: DAG Dispatch Foundation

## Feature Summary

ORC-63 promotes the orchestrator dispatcher from a linear list-walker to a DAG walker. Today `dispatch.py` selects the next step by scanning `workflow_plan[phase].active` in declaration order. This change introduces `depends_on` edges on plan nodes, synthesizes implicit chain dependencies for existing linear schemas (preserving byte-identical plan.yaml output), adds a hard prerequisite check that blocks dispatch when required inputs are unresolvable (completing the M2 `_resolve_inputs` stub), and adds two new CLI subcommands (`orchestrator ready` and `orchestrator graph`). It also enforces declared `outputs:` as a contract: a completed step whose declared outputs are absent on disk or in `evidence.outputs` fails. This is the foundation for parallel and meta-step dispatch on future tickets.

## Personas & Actors

- **Dispatch driver** (`skills/orchestrate/SKILL.md`, `skills/developer/SKILL.md`) — calls `orchestrator next` and `orchestrator done` in a loop; must be unchanged after this feature (AC-7).
- **Workflow author** — writes schema YAML (`config/workflows/feature.yaml`, etc.) with optional `depends_on:` on step entries; owns edge authoring.
- **Step agent** (developer, discoverer, architect, reviewer, etc.) — returns COMPLETION blocks; subject to AC-9 output post-check.
- **`record.py`** — validates done payloads; candidate enforcement point for AC-9.
- **`generate_plan.py`** — generates plan.yaml; must pass `depends_on` into plan nodes and cycle-detect at plan time (AC-6).
- **Operator / CI** — may call `orchestrator graph` for visibility or `orchestrator ready` for scripted multi-node dispatch.

## Use Cases

### Happy Path

UC-1: Linear schema runs unchanged — an operator runs a feature workflow using an existing linear schema; `generate_plan.py` synthesizes implicit chain edges, producing byte-identical plan.yaml; `dispatch.py` DAG-walks the chain and dispatches each step in the same order as before.

UC-2: Explicit depends_on used — a workflow author adds `depends_on: [explore]` to `design-and-draft-artifacts` in `feature.yaml`; plan.yaml gains a `depends_on` field on that node; dispatcher waits until `explore` is completed before selecting `design-and-draft-artifacts`.

UC-3: Prerequisite check blocks dispatch — a step contract declares `inputs: [discovery_result]` and the prior step that emits `discovery_result` has not completed; dispatcher exits 2 with a clear stderr reason instead of silently proceeding with missing data.

UC-4: `orchestrator ready` returns ready nodes — an operator calls `orchestrator ready <state.yaml>`; the CLI emits a JSON array of all nodes whose `depends_on` entries are all completed; `orchestrator next` returns `ready[0]` unchanged.

UC-5: `orchestrator graph` renders the DAG — an operator calls `orchestrator graph <state.yaml>`; the CLI emits a Mermaid or DOT representation of the current phase DAG with completion status per node.

UC-6: Optional input does not block — a step contract declares `inputs: [ux_direction]` as optional; that input is absent from step history; dispatcher proceeds without blocking.

UC-7: Output post-check enforces declared outputs — an agent returns COMPLETION for a step that declares `outputs: [discovery_result]` but does not write `discovery.md`; record.py rejects the done payload (or dispatch.py flags on next call) with a clear error.

### Error & Edge Cases

UC-E1: Cycle detected at plan time — a workflow author adds `depends_on` edges that form a cycle (A → B → A); `generate_plan.py` topo-sort detects the cycle and exits with a clear error before the plan is written. Dispatch never sees a cyclic graph.

UC-E2: depends_on targets a filtered step — a step declares `depends_on: [ux-design]` but `ux-design` was filtered from the plan due to `flags.ux_design=false`; plan-time behavior is unresolved (see OQ-1).

UC-E3: Missing required input at dispatch — a step declares a required input that is not in any completed step's `evidence.outputs` and not in `state.raw`; dispatcher exits 2 (blocked) with a diagnostic naming the missing input and the blocking step.

UC-E4: In-flight workflow using old `active:[ids]` shape — an active workflow in `spec/changes/` has the old `workflow_plan.main.active` list format; the new dispatcher must handle it without regression (backward compat) or a migration path must exist.

## Scope

### In Scope

- `workflow_plan[phase]` node model: `{nodes:[{id, depends_on}], verify}` for new workflows; `active:[ids]` remains valid (back-compat, synthesizes implicit chain deps)
- `dispatch.py`: replace `_phase_step_ids` linear scan with DAG-walk selecting first ready node (all `depends_on` entries completed), deterministic declaration-order tiebreak
- `dispatch.py`: promote `_resolve_inputs` missing list from no-op (lines 361, 364–368) to hard block (exit 2) for required inputs
- `parser.py`: `StepContract` gains `optional_inputs: list[str]` or equivalent to distinguish optional from required; optional missing inputs do not block
- `generate_plan.py`: pass `depends_on` from schema step entries into plan.yaml nodes; add topo-sort cycle detection at plan time
- New `orchestrator ready <state.yaml>` subcommand: returns JSON array of all currently-ready nodes
- New `orchestrator graph <state.yaml>` subcommand: renders DAG (Mermaid or DOT) from plan.yaml
- `inputs:`/`outputs:` normalization across all step contracts: remove prose qualifiers (e.g., `phase_context_bundle (includes discovery_result)` → `phase_context_bundle`)
- AC-9 output post-check: when a step completes, declared `outputs:` must exist on disk or as keys in `evidence.outputs`; missing declared outputs fail the step
- Existing tests must stay green; new tests for DAG-walk, prereq check, ready command, cycle detection, and output post-check

### Out of Scope

- Parallel dispatch — separate ticket (ORC-64/65)
- Runtime DAG expansion / meta-steps — separate ticket
- Dataflow inference of edges — edges are authored explicitly in schema step entries only
- Changes to step contracts for edge authoring — edges live in schema `steps:` entries, not in `config/steps/*.yaml` contracts (keeps contracts portable)
- Changes to dispatch drivers (`skills/orchestrate/SKILL.md`, `skills/developer/SKILL.md`) — AC-7: existing drivers unchanged
- New workflow schemas — only existing schemas gain implicit chain edges
- UI — no UI components

## UI Direction

N/A — no UI components.

## Key Decisions

- **Design direction**: Approach 2 — fold `plan.yaml` into `state.yaml`.
  `workflow_plan[phase]` promotes to `{nodes:[{id, depends_on, status, ...}],
  filtered, verify}`; `plan.yaml` is eliminated; per-node `status` replaces a
  separately-maintained `next_step` scan. Complexity L. Chosen by the change
  owner over the lower-complexity Approach 1 to consolidate workflow state
  into one file and simplify state updates. See design.md § Selected Approach.
- **OQ-1 (depends_on targets a filtered step)**: `generate_plan.py` drops the
  edge with a stderr warning. Hard-erroring would force conditional
  `depends_on` per optional-step flag; silent-satisfy is semantically wrong.
- **OQ-2 (phase_context_bundle)**: `phase_context_bundle` is **removed from
  all six contract `inputs:` lists** — not special-cased. A contract's
  `inputs:` enumerates real dataflow edges only (an upstream step's output or
  an artifact path). Engine-provided ambient context is delivered via env to
  every step and needs no declaration. Verified per contract: real edges are
  `discovery_result` (design-and-draft-artifacts, ux-design),
  `task_execution_result` (run-phase-review), `tasks.md` (execute-next-task);
  `explore`/`diagnose` open a phase with `inputs: []`. After the prune the
  prereq check needs no skip rule — every declared input is a real edge.
- **OQ-3 (output post-check location)**: `record.py` — upgrade the existing
  output check at lines 1189–1199 (currently dict-key presence only).
  Earliest feedback, no done-payload contract change.
- **OQ-4 (state.yaml shape)**: `plan.yaml` is **eliminated**;
  `workflow_plan[phase]` in `state.yaml` becomes
  `{nodes:[{id, depends_on, status, agent, goal, inputs, outputs, rules,
  repeat_until?}], filtered, verify}` and carries the graph, per-node status,
  and all per-step data formerly in `plan.yaml`. One file, one shape; node
  status is the source of truth for dispatch readiness.
- **OQ-5 (repeat_until + DAG readiness)**: a `repeat_until` node counts as a
  completed dependency for its dependents only when its `status` is
  `completed` AND its predicate evaluates True — promotes the condition
  already special-cased at `dispatch.py:319–323`.
- **OQ-6 (in-flight workflow migration)**: no migration script. Verified
  `generate_plan.py`'s only caller is `seed-state.sh` (init). An in-flight
  `state.yaml` with the legacy `active:[ids]` shape is handled by a parser
  back-compat read path that synthesizes bare `pending` nodes on read; the
  next full workflow init produces the `nodes` shape natively.
- **next_step retention**: `state.next_step` is kept as a *derived*
  convenience pointer (not deleted) — `resume-token.md` is an external
  contract, `seed-state.sh` writes it at init, `skills/orchestrate/SKILL.md`
  reads it on resume. `node.status` is the source of truth; `record.py`
  rewrites `next_step` from `next_ready_node` after every completion.
- **Shared readiness helper**: a single `readiness.py` module is the one DAG
  walker and the one `node.status` mutator, imported by both `dispatch.py`
  and `record.py` — verified `record._compute_next_step` is a second linear
  scanner; a shared helper prevents dispatched-vs-recorded drift.

## Open Questions

- OQ-1: **depends_on targets a filtered step.** If step B declares `depends_on: [ux-design]` and `ux-design` was filtered from the plan, does plan-time generation hard-error, silently treat the dependency as satisfied, or drop the edge? This needs resolution before the architect can finalize plan generation.

- OQ-2: **`phase_context_bundle` and the prereq hard block.** Several contracts declare `inputs: [phase_context_bundle]` but no step emits it as an output. `_resolve_inputs` currently falls back to `state.raw[name]`. Does AC-3 hard block apply to inputs resolvable from `state.raw`? Or is `phase_context_bundle` a sentinel always excluded from prereq checks? Needs explicit rule in the design.

- OQ-3: **AC-9 enforcement location.** Output post-check could live in `record.py` (reject the done payload immediately) or in `dispatch.py` (validate prior step's outputs before dispatching the successor). `record.py` gives earlier feedback; `dispatch.py` avoids changing the done payload contract. The ticket says "when an agent step completes" which leans toward `record.py`. Where it lives affects the failure UX and whether the done-payload contract changes.

- OQ-4: **State.yaml shape for `depends_on`.** Does `workflow_plan[phase]` in state.yaml itself change shape (from `active:[ids]` to `nodes:[{id,depends_on}]`) for new workflows, or does state.yaml always stay as `active:[ids]` with `depends_on` living only in plan.yaml? The ticket says plan.yaml is byte-identical for linear schemas but doesn't specify where the nodes shape lives — state.yaml or plan.yaml only.

- OQ-5: **`repeat_until` interaction with DAG readiness.** `execute-next-task` uses `repeat_until: all_tasks_completed`. In DAG terms, a downstream step that `depends_on: [execute-next-task]` — when is that dep considered "completed"? After the first completed entry (current linear behavior), or only after the predicate returns True? The current dispatch.py special-cases this at lines 314–323.

- OQ-6: **In-flight workflow migration.** Active workflows in `spec/changes/` (e.g., orc-30, orc-44, orc-58) have the `active:[ids]` shape. Is there an explicit migration step, or is backward compat handled transparently by treating `active:[ids]` as an implicit chain?

## Technical Context

### Key Files

| File | Path | Role |
|------|------|------|
| CLI entrypoint | `bin/orchestrator` | Subcommand router; current verbs: `next`, `done`, `record` (BC), `doctor`; new verbs: `ready`, `graph` |
| Dispatcher | `config/scripts/orchestrator_next/dispatch.py` | Pure function `dispatch(State, state_yaml_path) → (action, exit_code)` |
| Plan generator | `config/scripts/orchestrator_next/generate_plan.py` | Reads state+schema+contracts, writes plan.yaml |
| State/contract parser | `config/scripts/orchestrator_next/parser.py` | `State` dataclass, `StepContract` dataclass, contract loader |
| Record | `config/scripts/orchestrator_next/record.py` | Validates done payloads, appends step_history |
| Reconcile | `config/scripts/orchestrator_next/reconcile.py` | Reconciles in_progress state against DuckDB truth |
| Upsert | `config/scripts/orchestrator_next/upsert.py` | DuckDB step event upsert |
| Feature workflow schema | `config/workflows/feature.yaml` | Step list used by all feature workflows |
| Step contracts | `config/steps/*.yaml` | inputs:/outputs: declarations needing normalization |

### Precise Code Locations

- **Linear list reader being replaced**: `dispatch.py:72–77` (`_phase_step_ids`) reads `phase_plan.get("active", [])` — this is the function that becomes DAG-aware.
- **Linear next-step scan being replaced**: `dispatch.py:308–323` — the `for sid in step_ids` loop that picks the first non-completed step; must become a DAG-walk that checks `depends_on` readiness.
- **`_resolve_inputs` stub (M2 work)**: `dispatch.py:94–128` — returns `(resolved, missing)` tuple; the `missing` list is discarded at lines 361 and 287 (`_missing` unused); AC-3 promotes missing-required to exit 2.
- **M2 no-op comment**: `dispatch.py:364–368` — explicit comment "M1 note: missing inputs are NOT an error yet. Strict validation ... is M2's exit criterion." This is the exact stub to remove and replace.
- **plan.yaml step block builder**: `generate_plan.py:257–304` (`_build_step_block`) — must pass `depends_on` from schema step entry into plan node.
- **plan.yaml phase loop**: `generate_plan.py:337–384` — iterates `active_step_ids`; must shift to nodes with edges.
- **StepContract dataclass**: `parser.py:31–47` — needs `optional_inputs: list[str]` field (or equivalent annotation) for AC-4.
- **`_load_contract`**: `parser.py:101–155` — parses contract YAML; must handle optional input annotations.

### Current workflow_plan Structure (state.yaml)

```yaml
workflow_plan:
  main:
    active:
      - explore
      - design-and-draft-artifacts
      - ...
    filtered:
      - {id: ux-design, reason: flag ux_design=false}
    verify:
      ...
```

### Current plan.yaml Structure (per-step block)

```yaml
- agent: discoverer
  goal: ''
  id: explore
  inputs: [phase_context_bundle]
  outputs: [discovery_result]
  rules: [...]
```

After ORC-63, plan nodes gain optional `depends_on: [step_id, ...]`. Absent = synthesized implicit chain dep (previous step in declaration order).

### Inputs/Outputs Normalization Needed

Current prose qualifiers in step contracts that must be cleaned up (AC-5):

- `design-and-draft-artifacts.yaml`: `inputs: [phase_context_bundle (includes discovery_result), ux-artifacts.yaml (optional, per contracts/ux-artifacts.md)]` — both need normalization
- `generate-project-yaml.yaml`: `inputs: [detect-language outputs (languages, package_manager, web_project, backend_project), install-tooling outputs (scripts_added)]` — prose prefixes must be stripped
- `install-tooling.yaml`: `inputs: [detect-language outputs (languages, package_manager, web_project)]` — prose prefix
- `run-ux-critique.yaml`: `inputs: [Files modified in current phase that touch UI, project context (quality_bar, vision.target_users from project.yaml)]` — both are prose descriptions
- `run-phase-review.yaml`: `inputs: [task_execution_result, phase_context_bundle]` — second is prose-clean but first also appears in `execute-next-task.yaml` outputs as plain `task_execution_result` — these align

### CLI Surface Inventory (Mandatory per step contract)

**`bin/orchestrator` subcommands (current):**
- `next <state.yaml>` — dispatch, emits action JSON or exits 1/2/3
- `done <state.yaml>` — record step completion (JSON on stdin)
- `record <state.yaml>` — silent BC alias for `done`
- `doctor` — environment check

**`bin/orchestrator` subcommands (new, this ticket):**
- `ready <state.yaml>` — AC-7: returns JSON array of all currently-ready nodes
- `graph <state.yaml>` — AC-8: renders DAG (Mermaid or DOT) from plan.yaml

**`orchestrator_next/` Python modules (all touched or adjacent):**
- `dispatch.py` — primary change (DAG walk, prereq hard block)
- `generate_plan.py` — add depends_on passthrough + cycle detection
- `parser.py` — StepContract gains optional_inputs
- `record.py` — AC-9 output post-check
- `reconcile.py`, `upsert.py`, `resolver.py`, `doctor.py`, `cost_report.py`, `jsonl_usage.py` — no changes expected

**`config/scripts/inline/*.sh`** (~22 scripts): archive-completed-change.sh, bootstrap-commit.sh, capture-test-baseline.sh, check-bootstrap-state.sh, compute-prediction-accuracy.py/.sh, compute-swe-metrics.sh, detect-language.sh, git-init.sh, mark-change-completed.sh, merge-to-main.sh, preview-route.sh, register-with-orchestrator-home.sh, remove-worktree.sh, run-quality-baseline.sh, setup-claude-md.sh, setup-claude-settings.sh, setup-portless.sh, verify-report.sh, write-bootstrap-state.sh, append-retro.sh, _read_state_env.sh — **none need changes for this ticket**.

**Dispatch drivers (backward compat surface — must remain unchanged per AC-7):**
- `skills/orchestrate/SKILL.md` line 131: `orchestrator next $WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml`
- `skills/developer/SKILL.md` line 47: `orchestrator next "$STATE_FILE"`
- Both call `orchestrator done` for step recording — interface contract unchanged.

### Library / Runtime Versions

- Python: standard library + `pyyaml`, `duckdb` (per `bin/orchestrator` header comment)
- No external DAG libraries in use; topo-sort is a ~10-line stdlib implementation
- Test runner: `pytest` (from `project.yaml verify_commands.test`)
