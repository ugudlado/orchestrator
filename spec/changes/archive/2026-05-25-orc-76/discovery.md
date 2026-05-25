---
feature-id: orc-76
linear-ticket: none
---

# Discovery Brief: Reorganize steps as directories + typed file I/O on contracts

## Feature Summary

Today a step contract is a single mixed YAML file that bundles schema metadata (id, version, kind, inputs, outputs, depends_on) with a large English instruction block and an ever-growing rules list. For the 8 `run:` step contracts, the contract lives in `config/steps/<id>.yaml` but its script lives in `config/scripts/inline/<id>.sh` — a 1:1 pair split across two directory trees. This feature reorganizes each step into a directory (`config/steps/<id>/`) holding `contract.yaml` (typed schema only) and exactly one payload file (`prompt.md` for agent steps, `script.sh` for script steps). Additionally, the feature changes `inputs:/outputs:` semantics from named values resolved from prior step evidence to typed file-path references with pre/post existence validation.

## Personas & Actors

- **Workflow engine** (`parser.py`, `dispatch.py`, `generate_plan.py`) — loads and interprets step contracts; must find and parse `contract.yaml` from the new directory layout.
- **Orchestrator CLI** (`bin/orchestrator`) — resolves script paths from `contract.run`; path resolution logic changes with the new layout.
- **Agent steps** (discoverer, architect, developer, reviewer, ux-reviewer, ideator, workflow-learner) — receive instruction text from `prompt.md`; the learn cycle appends rules to `prompt.md`.
- **Script steps** (archive-completed-change, capture-test-baseline, complete-workflow, compute-swe-metrics, compute-prediction-accuracy, expand-plan, mark-change-completed, preview-route) — script is co-located in the step directory.
- **Workflow author / repo operator** — adds or edits step contracts; co-location removes the need to keep two files in sync across directories.
- **Learn cycle** — appends learned rules to agent step prompts; target changes from `contract.rules[]` to `prompt.md` append.

## Use Cases

### Happy Path

UC-1: Contract load for an agent step — the dispatcher wants to load the `explore` step contract so that it can build the agent spawn action. `_load_contract` searches `config/steps/explore/contract.yaml`, reads id/agent/inputs/outputs, then reads `config/steps/explore/prompt.md` for the instruction and rules.

UC-2: Contract load for a script step — the dispatcher wants to load the `expand-plan` step contract so that it can run the inline script. `_load_contract` reads `config/steps/expand-plan/contract.yaml` for id/run, then the `run` value resolves to the co-located `config/steps/expand-plan/script.sh` (or an absolute/relative canonical path in the yaml).

UC-3: Pre-step file validation — before dispatching `design-and-draft-artifacts`, the dispatcher checks that `inputs[0].path` (`spec/changes/<slug>/discovery.md`) exists on disk so that the step is not blocked by a missing artifact.

UC-4: Post-step file validation — after `explore` completes, `record.py` verifies that `outputs[0].path` (`spec/changes/<slug>/discovery.md`) exists on disk before accepting the COMPLETION payload.

UC-5: Learn-cycle rule append — after a completed feature, the learn cycle appends a new rule string to `config/steps/<id>/prompt.md` rather than to the `rules:` list in a single YAML file.

### Error & Edge Cases

UC-E1: Missing contract directory — `_load_contract` cannot find `config/steps/<id>/` and raises `FileNotFoundError` with a diagnostic listing searched directories.

UC-E2: Missing payload file — `contract.yaml` exists but `prompt.md` (for an agent step) is absent. Dispatcher raises `ContractError` with a message naming the missing payload.

UC-E3: Pre-step input file absent — `inputs[0].path` does not exist at dispatch time (upstream step did not produce it). Dispatcher blocks with exit 2 and a diagnostic naming the missing file path.

UC-E4: Post-step output file absent — `outputs[0].path` does not exist after the step completes. `record.py` rejects the payload with `missing_outputs` (exit 3).

UC-E5: Script not executable / not found — `contract.yaml` declares `run: script.sh` but the file is absent from the step directory. Dispatcher raises a `ContractDispatchError`.

## Scope

### In Scope

- Migrate all 16 dispatch-engine `config/steps/*.yaml` contracts to directory layout: `config/steps/<id>/contract.yaml` + payload (`prompt.md` or `script.sh`). (`select-workflow.yaml` is skill-level pre-init — see Technical Context.)
- Update `parser.py:_load_contract` and `_contract_search_dirs` to look for `contract.yaml` in directory form (with backward-compat for flat-file layout during transition).
- Update `dispatch.py` and `bin/orchestrator` `_run_path` resolution to handle co-located `script.sh` in the step directory.
- Update `generate_plan.py:_load_step_contract_raw` and `_build_step_block` to load `contract.yaml` from directory.
- Define canonical home for shared helpers (`_read_state_env.sh`, `append-retro.sh`) that are not step-specific — a stated location, not a step directory.
- Change `inputs:/outputs:` in `contract.yaml` from named-value strings to typed file-path objects (`{path: ..., template: ...}`) for agent steps.
- Update `dispatch.py:_check_required_inputs` and `_resolve_inputs` to check file existence rather than walk `evidence.outputs`.
- Update `record.py` declared-outputs enforcement to check `os.path.exists(path)` rather than `evidence.outputs[name]`.
- Delete `config/steps/contracts/artifact-formats.md`; redistribute format rules to producer step `prompt.md` files and reviewer step `prompt.md`.
- Update `config/scripts/orchestrator_next/tests/` that assert on flat-file contract paths or `run:` strings.

### Out of Scope

- Migrating the `bootstrap` workflow scripts (`git-init.sh`, `bootstrap-commit.sh`, `detect-language.sh`, `check-bootstrap-state.sh`, `write-bootstrap-state.sh`, `run-quality-baseline.sh`, `setup-claude-md.sh`, `setup-claude-settings.sh`, `setup-portless.sh`, `register-with-orchestrator-home.sh`) — these are not wired to any `config/steps/*.yaml` contract and are invoked by bootstrap skill flows outside the dispatch engine. They are sub-helpers and skill-invoked scripts with a separate caller chain. Moving them would require a separate skill-level refactor.
- Migrating `validate-tasks-yaml.sh` and `verify-report.sh` — called directly by step prompts and tests, not dispatched as steps.
- Converting `compute-prediction-accuracy.py` — a Python script invoked by `compute-prediction-accuracy.sh`; it is a sub-helper, not a step entrypoint in its own right.
- Migrating `select-workflow.yaml` — this is a skill-level pre-init contract invoked by `skills/orchestrate` before workflow init, NOT placed inside any workflow's `steps:` list. It is not dispatched by the engine and does not follow the same migration path.
- Runtime behavior changes beyond file I/O semantics (no changes to DAG scheduling, phase gating, retry logic, or the `orchestrator done` payload contract itself).
- Repo-override mechanism changes (`$REPO_ROOT/.orchestrator/steps/`) — directory layout there follows the same pattern but the override priority logic in `_contract_search_dirs` is otherwise unchanged.
- Removing the flat-file `contract.yaml` tests immediately — a backward-compat read path is acceptable for one cycle.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Build (refactor in place)** — this is internal layout reorganization of the orchestrator engine itself. No external library, off-the-shelf step runner, or reusable solution applies. The existing `parser.py`, `dispatch.py`, `generate_plan.py`, and `bin/orchestrator` code is extended and adapted — not replaced. The two sub-changes (directory reorg + typed file I/O) are independently scoped; whether they land together or sequentially is an open question (OQ-1), but both are build-in-place work with no viable reuse alternative.

- **Selected design (architect, 2026-05-25)**: Approach 1 — directory-per-step + typed file I/O, bundled into one feature staged as Stage A (layout) and Stage B (typed I/O). Complexity: L. Rules ruling out alternatives: the bundled approach amortises the parser/dispatch/record edits across one feature instead of two cycles; script-payloads stay as runnable shell files (rules out single-file-with-embedded-payload alternative).

- **OQ-1 resolved**: Bundle. Stage A migrates layout; Stage B converts I/O semantics. Each stage independently green.
- **OQ-2 resolved**: Non-step helpers stay in `config/scripts/inline/`. Step scripts move into `config/steps/<id>/script.sh`. Single canonical location per concern.
- **OQ-3 resolved**: `run:` in `contract.yaml` is relative to the contract directory (e.g. `run: script.sh`). Resolver: `{contract_dir}/{run}` when relative.
- **OQ-4 resolved**: `record.py:1662` path is fixed to `config/scripts/inline/append-retro.sh` (the stale `scripts/inline/` lookup is corrected).
- **OQ-5 resolved**: `artifact-formats.md` is deleted; each producer step's `prompt.md` carries its own section, and `run-phase-review/prompt.md` carries the consolidated copy.
- **OQ-6 resolved**: `rules:` stays in `contract.yaml`. Only the instruction prose moves to `prompt.md`. The learn cycle's YAML list-append semantics are preserved.
- **`kind:` is explicit** in `contract.yaml` (`agent` or `script`). No inference from payload presence.
- **`<slug>` placeholder** is the substitution syntax in typed paths; resolved against `state.change_id` at dispatch time.

## Open Questions

- OQ-1: Are the typed file I/O semantics (`inputs: [{path: ...}]`) in scope for this feature, or should directory reorg land first as a pure layout change with a follow-up for I/O semantics? The two are independently scoped and each touches different parts of the engine (`_load_contract` / path resolution vs. `_resolve_inputs` / `record.py` declared-outputs). Bundling them is the feature description's intent but creates a larger blast radius.

- OQ-2: What is the canonical home for shared helpers that are not step-specific? Candidates: (a) `config/scripts/helpers/` — a new directory, single source of truth; (b) `config/scripts/inline/` stays as-is for shared helpers only, with step scripts migrated into their directories. The "single canonical location invariant" constraint rules out any approach that leaves helpers in multiple homes.

- OQ-3: For the `run:` field in `contract.yaml` for script steps: does it hold a relative path (`script.sh` — resolved relative to the step directory) or remain an absolute/repo-relative path (`config/steps/expand-plan/script.sh`)? The relative form is simpler and enforces co-location; the absolute form preserves backward compat with the `_run_path` resolver in `bin/orchestrator` (lines 359–363).

- OQ-4: How does `record.py`'s `append-retro.sh` call path change? Currently `record.py:1662` looks for `append-retro.sh` in `$REPO_ROOT/scripts/inline/` then `$ORCHESTRATOR_HOME/scripts/inline/` (note: different from `config/scripts/inline/` — this looks like a stale path). This path must be updated regardless of whether shared helpers move.

- OQ-5: Does deletion of `artifact-formats.md` require `run-phase-review`'s `prompt.md` to carry its own copy of all five format contracts, or does the reviewer reference individual producer prompts? If the reviewer reads producer prompts directly, how does it find them at review time?

- OQ-6: What is the internal structure of `prompt.md` for agent steps? The current flat YAML contract separates `instruction:` (prose body) from `rules:` (appendable list). When the learn cycle appends a new rule to `prompt.md`, it must know where in the file to append — and that requires a defined internal section boundary. Does `prompt.md` have a structured `## Rules` section that the learn cycle targets? Or do rules remain in `contract.yaml` and only the instruction prose moves to `prompt.md`? This decision affects the parser shape and the learn cycle's file-write logic.

## Technical Context

### Callable Entrypoints (CLI Surface Inventory)

**`bin/orchestrator` subcommands:**
- `orchestrator next <state.yaml>` — dispatch next step
- `orchestrator done <state.yaml>` — record step completion (JSON on stdin)
- `orchestrator ready <state.yaml>` — list ready DAG node IDs
- `orchestrator graph <state.yaml>` — render Mermaid DAG
- `orchestrator expand-plan <state.yaml>` — append task-nodes from tasks.yaml
- `orchestrator doctor` — health check
- `orchestrator record <state.yaml>` — backward-compat alias for `done`

**Step contracts — dispatch-engine steps (16 files under `config/steps/`):**

Agent steps (8):
- `config/steps/design-and-draft-artifacts.yaml` — agent: architect
- `config/steps/diagnose.yaml` — agent: discoverer
- `config/steps/execute-one-task.yaml` — agent: developer
- `config/steps/explore.yaml` — agent: discoverer
- `config/steps/run-learn-cycle.yaml` — agent: workflow-learner
- `config/steps/run-phase-review.yaml` — agent: reviewer
- `config/steps/run-ux-critique.yaml` — agent: ux-reviewer
- `config/steps/ux-design.yaml` — agent: ideator

Script steps (8, each with a paired `config/scripts/inline/<id>.sh`):
- `config/steps/archive-completed-change.yaml` → `archive-completed-change.sh`
- `config/steps/capture-test-baseline.yaml` → `capture-test-baseline.sh`
- `config/steps/complete-workflow.yaml` → `complete-workflow.sh`
- `config/steps/compute-prediction-accuracy.yaml` → `compute-prediction-accuracy.sh`
- `config/steps/compute-swe-metrics.yaml` → `compute-swe-metrics.sh`
- `config/steps/expand-plan.yaml` → `expand-plan.sh`
- `config/steps/mark-change-completed.yaml` → `mark-change-completed.sh`
- `config/steps/preview-route.yaml` → `preview-route.sh`

Skill-level pre-init contract (NOT a dispatch-engine step — excluded from migration):
- `config/steps/select-workflow.yaml` — invoked by `skills/orchestrate` before workflow init; not placed inside any workflow's `steps:` list; does not go through the dispatch engine path.

**Inline scripts NOT wired to step contracts (must not move into step directories):**

Sub-helpers (called by step scripts):
- `config/scripts/inline/_read_state_env.sh` — sourced by `complete-workflow.sh`, `archive-completed-change.sh`, `merge-to-main.sh`, `remove-worktree.sh`
- `config/scripts/inline/merge-to-main.sh` — sub-invoked by `complete-workflow.sh`
- `config/scripts/inline/remove-worktree.sh` — sub-invoked by `complete-workflow.sh`

Skill-invoked or bootstrap-only scripts (no dispatch wiring):
- `bootstrap-commit.sh`, `check-bootstrap-state.sh`, `detect-language.sh`, `git-init.sh`, `register-with-orchestrator-home.sh`, `run-quality-baseline.sh`, `setup-claude-md.sh`, `setup-claude-settings.sh`, `setup-portless.sh`, `write-bootstrap-state.sh`

Utility scripts (called by engine or tests directly):
- `append-retro.sh` — called by `record.py:1662` (path: `$ORCHESTRATOR_HOME/scripts/inline/` — note stale path, see OQ-4)
- `validate-tasks-yaml.sh` — called by `run-phase-review` agent prompt instruction; invoked directly in tests
- `verify-report.sh` — utility, callers TBD
- `compute-prediction-accuracy.py` — Python sub-script invoked by `compute-prediction-accuracy.sh`

### Key Source Files and Integration Points

- `config/scripts/orchestrator_next/parser.py`
  - `_contract_search_dirs` (lines 81–101) — returns ordered search dirs; must handle directory layout
  - `_load_contract` (lines 104–173) — looks for `{step_id}.yaml`; must also try `{step_id}/contract.yaml`
  - `StepContract` dataclass (lines 31–50) — `inputs` and `outputs` currently `list[str]`; typed file I/O changes these to `list[dict]`
  - inline-script branch (lines 116–132) — keys off `inline: true` or `run:`; with directory layout, `kind:` could be inferred from payload file presence

- `config/scripts/orchestrator_next/dispatch.py`
  - `_resolve_inputs` (lines 75–109) — walks `evidence.outputs` by name; file I/O changes this to existence check
  - `_check_required_inputs` (lines 197–218) — blocks on missing named inputs; changes to missing file paths
  - `_run_path` resolution in `bin/orchestrator` (lines 359–363) — prepends `$ORCHESTRATOR_HOME/config/`; co-located script.sh needs different resolution

- `config/scripts/orchestrator_next/generate_plan.py`
  - `_load_step_contract_raw` (lines 58–72) — searches same dirs as `parser._contract_search_dirs`; needs directory-form lookup
  - `_build_step_block` (lines 245–300) — reads `agent`, `inputs`, `outputs`, `rules`, `repeat_until` from raw YAML; these fields live in `contract.yaml` under new layout

- `config/scripts/orchestrator_next/record.py`
  - Declared-outputs enforcement (referenced in `done-payload.md`) — currently checks `evidence.outputs[name]`; file I/O changes to `os.path.exists(path)`
  - `append-retro.sh` call at line 1662 — stale path (searches `scripts/inline/` not `config/scripts/inline/`)

- `config/steps/contracts/artifact-formats.md` — to be deleted; format rules redistributed to producer step `prompt.md` files

### Library Versions and Dependencies

- Python 3.x, PyYAML — no version changes required
- All parsing is pure YAML/filesystem; no new dependencies
