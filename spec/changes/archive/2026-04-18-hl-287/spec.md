---
feature-id: hl-287
linear-ticket: HL-287
---

# HL-287 — Orchestrator-Worker Audit + Rework Plan

## Motivation

The orchestrator dispatches against **45 named step contracts** under
`$ORCHESTRATOR_HOME/config/steps/*.yaml`. Only **8** represent genuine cognitive
work that requires an LLM spawn. The remainder are mechanical bookkeeping
wrapped in full agent invocations, paying LLM cost and latency for
deterministic work. Cross-artefact drift between step contracts, agent
definitions, and workflow schemas has already caused first-review failures
(e.g. `claude-discoverer.sh` vs `claude_discoverer.py`).

This feature does two things in one pass:

1. **Audit** — categorizes every feature/bugfix/spike-lifecycle step contract,
   resolves every ambiguous case via explicit user decisions, identifies the
   three misclassified-math steps, and defers bootstrap.
2. **Rework Plan** — a single, holistic execution plan organized by
   **milestones with dependency ordering**, not by scope ticket. What were
   previously framed as two follow-up specs (scope #2 refactor, scope #3
   agent-role alignment) are now interleaved into one sequenced rollout with
   shared exit criteria.

The framing change matters: scope #2 and scope #3 share prerequisites
(typed-dispatcher foundation), share risks (workflow-schema resolution), and
share a reviewer gate. Splitting them into two tickets forced artificial
serialisation and duplicated the cross-cutting CI-grep work. One plan, one
dependency graph, one gate.

## What Changes

A consolidated `spec.md` under `.spec/hl-287/` containing (a) the canonical
audit, (b) the ambiguity resolutions, (c) the bootstrap deferral stub, and
(d) a single `§ Rework — Execution Plan` with milestones M1..M8. No
orchestrator code changes in this feature. Follow-on code work is the
milestone-by-milestone execution, tracked against this plan.

## Requirements

### Functional

1. **FR-1**: The audit categorizes every feature/bugfix/spike-lifecycle step
   contract currently in `$ORCHESTRATOR_HOME/config/steps/`. The canonical
   table is § *Audit — Canonical Categorization* below.
2. **FR-2**: Every row of the categorization table has non-empty
   `target_category`, `target_agent / target_function`, `rationale`, and
   `evidence` columns. Evidence cites a file path + contract field or line
   reference.
3. **FR-3**: `target_category` is drawn from the closed set
   `agent-driven | dispatcher-primitive | inline | fold-into`. No row is
   `ambiguous`.
4. **FR-4**: The four user decisions made during discovery are encoded
   verbatim: (a) `phase-signoff` folds into `run-phase-review`, (b)
   `verify-spike-findings` becomes a spike-specific sub-rubric inside
   `run-phase-review` (not a Python hook), (c) bootstrap-schema steps are
   out of scope, (d) the three misclassified-math steps become inline Python
   functions.
5. **FR-5**: § *Misclassified-Math Steps* names
   `compute-swe-metrics`, `compute-prediction-accuracy`, and
   `archive-completed-change` with evidence.
6. **FR-6**: § *Rework — Execution Plan* contains a sequenced list of
   milestones (count in [6, 10]) that collectively execute every fold-into
   row from the audit table and every action from the agent-role consolidation
   map. Each milestone carries Goal, Exit Criteria, Dependencies, Agent(s),
   Concrete Changes, Risk / Rollback, and Size.
7. **FR-7**: A bootstrap follow-up stub is included — one paragraph at the
   end of the audit section — tracking the bootstrap-schema steps as a
   separate future ticket.

### Non-Functional

1. **NFR-1**: Every evidence cell in the categorization table cites a real
   path under `$ORCHESTRATOR_HOME/config/steps/` (or relative to repo root)
   and a field name or line reference. A reviewer must be able to jump from
   a row to the evidence in one hop.
2. **NFR-2**: The spec is plain Markdown, readable top-to-bottom without
   external tools. No generated diagrams.
3. **NFR-3**: The milestone dependency graph is acyclic. Every listed
   dependency refers to an earlier milestone ID or `none`.

## Architecture

Single-document audit + holistic rework plan. No code produced in this
feature. Files touched:

| File                       | Change    |
|----------------------------|-----------|
| `.spec/hl-287/spec.md`     | Absorbs audit + single `§ Rework — Execution Plan` |
| `.spec/hl-287/discovery.md`| Unchanged — raw input                          |
| `.spec/hl-287/design.md`   | Adds "Why a Single Execution Plan" paragraph   |
| `.spec/hl-287/tasks.md`    | T-1/T-2/T-3 verify structural shape of the plan |

## Test Strategy

### Test File Paths

N/A — the artefact is a Markdown document. Verification is script-based
structural validation (see `tasks.md`).

### Coverage Targets

Structural completeness: 100% of the 31 in-scope step contracts appear in the
categorization table. Every user decision is encoded. The execution plan
covers every fold-into row and every scope-3 consolidation map entry via at
least one milestone's Concrete Changes.

### Key Test Scenarios

- Every step_id from discovery's four feature-lifecycle subtables appears
  exactly once in the categorization table.
- Every `target_category` is from the closed set.
- The three misclassified-math step_ids appear in § *Misclassified-Math Steps*.
- § *Rework — Execution Plan* contains between 6 and 10 milestones; every
  milestone has the required fields; dependency graph is acyclic.
- Every fold-into audit row maps to ≥1 milestone Concrete Change; every
  scope-3 consolidation map entry maps to ≥1 milestone Concrete Change.

## Acceptance Criteria

- **AC-1**: § *Audit — Canonical Categorization* contains a table with exactly
  31 data rows covering every in-scope step contract. Every row has non-empty
  `target_category`, `target_agent / target_function`, `rationale`, and
  `evidence`. No row carries `target_category: ambiguous`. [traces: UC-1, UC-2]
- **AC-2**: § *Misclassified-Math Steps* names the three misclassified step_ids
  (`compute-swe-metrics`, `compute-prediction-accuracy`,
  `archive-completed-change`) and maps each to an inline script under
  `scripts/inline/`. [traces: UC-2]
- **AC-3**: § *Ambiguous Cases — Resolved* documents resolutions for all four
  user decisions (AQ-1 phase-signoff, AQ-2 verify-spike-findings, AQ-3
  bootstrap, AQ-4 generate-project-yaml / setup-makefile). [traces: UC-E1]
- **AC-4**: § *Rework — Execution Plan* contains between 6 and 10 milestones
  inclusive, each with a unique ID of the form `M<n>`. [traces: UC-2]
- **AC-5**: Every milestone declares all of: **Goal** (one sentence),
  **Exit Criteria** (≥2 testable bullets), **Dependencies** (list of earlier
  milestone IDs or `none`), **Agent(s)**, **Concrete Changes** (file-path-level
  deliverables), **Risk / Rollback**, **Size** (XS / S / M / L). [traces: UC-2]
- **AC-6**: Dependency graph is acyclic — every listed dependency is either
  `none` or refers to a milestone whose ID number is strictly lower than the
  declaring milestone. [traces: UC-2]
- **AC-7**: Every `fold-into` row in the audit table maps to at least one
  milestone's Concrete Changes (the target step ID appears verbatim somewhere
  under a milestone's Concrete Changes bullets). [traces: UC-2]
- **AC-8**: Every action in the scope-3 Consolidation Map (rename, retire,
  relocate) maps to at least one milestone's Concrete Changes. [traces: UC-2]
- **AC-9**: Bootstrap is reconfirmed as out-of-scope in § *Bootstrap Follow-Up
  Stub*. [traces: UC-1]
- **AC-10**: § *Role Definition Template* (inside M7 in the execution plan)
  lists the five named sections (Purpose, Philosophy, Responsibilities,
  Constraints, Evidence standards) AND contains an explicit note that typed
  I/O lives on step contracts, NOT on role definitions. [traces: UC-E1]

## Alternatives Considered

- **Two separate follow-up specs (scope #2, scope #3)**: original framing.
  Rejected once the dependency structure made it clear both scopes share the
  typed-dispatcher prerequisite (M1), the workflow-schema resolution risk, and
  the final CI-grep gate. Splitting would duplicate that cross-cutting work
  and force an artificial serialisation.
- **One giant ticket with no milestones**: rejected — loses the ability to
  ship incrementally and to checkpoint risk at milestone boundaries.
- **Three separate deliverable files** (`audit.md`, two scope specs):
  rejected as merge target. The feature-schema expects a canonical
  four-artefact set; extras introduced file-sprawl and trace-reference
  fragility. Content merged into this `spec.md` as named sections.

## Phase Gate Notes

This feature is documentation-only. Several `project.yaml` quality gates do
not semantically apply:

- **`test_coverage` (≥90%)**: no code produced. Reviewer should not enforce.
- **`tdd_required: true`**: no tests to write first. Tasks in `tasks.md` are
  post-hoc structural checks, not a red-green-refactor cycle. Reviewer scores
  on document-quality dimensions (completeness, evidence, actionability).
- **`min_phase_review_score: 9`**: still applies, scored on document quality.

The implement phase runs to satisfy workflow schema requirements but produces
no code. See `design.md` for rationale.

## Impact

No breaking changes. No code or agent contract modifications in this feature.
Follow-on work is the milestone-by-milestone execution plan below — each
milestone is independently shippable and reversible via `git revert`.

## Decisions

- **Single-spec, single-plan format** (audit + execution plan in one
  `spec.md`): selected to keep the artefact set to the canonical
  feature-schema four. Replaces the earlier two-follow-up-spec framing.
- **Holistic milestone plan over separate scope tickets**: selected because
  scope #2 and scope #3 share the typed-dispatcher prerequisite (M1), the
  workflow-schema resolution risk, and the final CI-grep gate.
- **`phase-signoff` → folds into `run-phase-review`** (user decision #1).
- **`verify-spike-findings` → spike sub-rubric inside `run-phase-review`**
  (user decision #2; not a Python hook).
- **Bootstrap schema categorization → deferred** (user decision #3). Stub below.
- **Misclassified-math steps → inline scripts** under `scripts/inline/` (shell
  by default; Python where shell hurts) (user decision #4).
- **The `orchestrate` skill stays** — shrunk to ~20-line dispatch loop. Not
  replaced by a Python dispatcher.
- **Two step categories only** — `agent-driven` and `inline`. No separate
  "hook subsystem". Dispatcher primitives (`autopilot-*`) retain contracts
  but are absent from feature/bugfix/spike schema step lists.
- **Typed I/O lives on step contracts, NOT agent role files**. The same role
  may be invoked with different I/O shapes across workflows.
- **Per-step stamping is universal** — applied by the Python helper around
  every step, not declared per-contract.

---

## Audit — Canonical Categorization

One row per feature/bugfix/spike-lifecycle step contract. Close-set for
`target_category`: `agent-driven | dispatcher-primitive | inline | fold-into`.

**Note**: All non-agent steps run as `inline` — executed by the Python helper
directly. Their relative position in a schema's step list decides whether they
run before or after agent-driven steps (no separate hook mechanism). The
per-step stamping (`started_at`, `completed_at`, `status`, `usage`, `evidence`)
is done uniformly and automatically by the helper around every step — not
declared per-contract.

The `target_agent / target_function` column carries two semantics: for
`agent-driven` rows, it names the **target_agent** (role name); for `inline`
rows, it names the **target_function** — a script at
`scripts/inline/<step-id>.{sh|py}`. Script names in the table below use
underscores-for-hyphens only as a readability convention; actual file names
match the step_id verbatim.

| step_id | current_mode | target_category | target_agent / target_function | rationale | evidence |
|---|---|---|---|---|---|
| explore | agent=discoverer | agent-driven | discoverer | Core discovery: surveys codebase, writes discovery.md | `explore.yaml` `agent: discoverer` |
| diagnose | agent=discoverer | agent-driven | discoverer | Bug root-cause tracing; LLM reasoning required | `diagnose.yaml` `agent: discoverer` |
| design-and-draft-artifacts | agent=architect | agent-driven | architect | Primary design step (absorbs design-exploration + create-or-refresh-artifacts + validate-artifacts) | `design-and-draft-artifacts.yaml` `agent: architect`, Parts 1–3 |
| execute-next-task | agent=developer | agent-driven | developer | Core implementation loop with per-task reasoning + retry | `execute-next-task.yaml` `agent: developer` |
| run-phase-review | agent=reviewer | agent-driven | reviewer | Quality gate: scores dimensions, emits fix tasks, **absorbs** final-signoff + phase-signoff + run-implement-review + verify-spike sub-rubric | `run-phase-review.yaml` `agent: reviewer`, §5c |
| run-ux-critique | agent=ux-reviewer | agent-driven | ux-reviewer | UI quality gate (flag-gated on `ux_design`) | `run-ux-critique.yaml` `agent: ux-reviewer` |
| ux-design | agent=ideator | agent-driven | designer (rename from ideator; see M6) | UI prototyping; current `ideator` agent is the wrong role | `ux-design.yaml` `agent: ideator` |
| run-learn-cycle | agent=workflow-improver | agent-driven | learner (rename from workflow-improver; see M6) | Learning trigger; invokes `/learn` skill | `run-learn-cycle.yaml` `agent: workflow-improver` |
| autopilot-iterate | inline | dispatcher-primitive | autopilot-iterate | Autopilot loop body; not a workflow step | `autopilot-iterate.yaml` (no agent field) |
| autopilot-preflight | inline | dispatcher-primitive | autopilot-preflight | Pre-flight checks for autonomous execution | `autopilot-preflight.yaml` (no agent field) |
| autopilot-session-report | inline | dispatcher-primitive | autopilot-session-report | Session summary printer | `autopilot-session-report.yaml` (no agent field) |
| create-worktree | inline | inline | `scripts/inline/create-worktree.sh` | `git worktree add` + `.env` symlink + deps install; no reasoning | `create-worktree.yaml` — all bash |
| load-project-context | inline | inline | `scripts/inline/load-project-context.py` (Python — YAML merge grim in shell) | Reads project.yaml + schema, computes workflow_plan; deterministic YAML merge | `load-project-context.yaml` — file reads + merge |
| configure-gitignore | inline | inline | `scripts/inline/configure-gitignore.sh` | Appends entries to `.gitignore` per language matrix | `configure-gitignore.yaml` — append-only file ops |
| check-bootstrap-state | inline | inline | `scripts/inline/check-bootstrap-state.sh` | Reads `.tooling-state.json`; gates bootstrap continuation | `check-bootstrap-state.yaml` — read + compare |
| capture-test-baseline | inline | inline | `scripts/inline/capture-test-baseline.sh` | Runs test command, parses counts, writes `baseline:` | `capture-test-baseline.yaml` — run + parse + write |
| preview-route | inline | inline | `scripts/inline/preview-route.sh` | Runs `estimate-cost.sh`; appends `route_preview:` block | `preview-route.yaml` `instruction: 1. Run the estimator` |
| autopilot-session-init | inline | inline | `scripts/inline/autopilot-session-init.sh` | Creates sessions.yaml entry + `_checkpoint.json` | `autopilot-session-init.yaml` (no agent field) |
| create-linear-ticket | inline | inline | `scripts/inline/create-linear-ticket.sh` | API call to Linear (curl); deterministic; flag-gate is a top-of-script `[[ "$LINEAR" == "true" ]] \|\| exit 0` | `create-linear-ticket.yaml` (no agent field) |
| mark-change-completed | inline | inline | `scripts/inline/mark-change-completed.sh` | Writes `status: completed`, `completed_at`, `archive_path` via yq | `mark-change-completed.yaml` inline comment |
| **compute-swe-metrics** | **agent=developer** | inline | `scripts/inline/compute-swe-metrics.sh` (relocated from `config/scripts/`) | **MISCLASSIFIED** — invokes existing `.sh`; no LLM reasoning | `compute-swe-metrics.yaml` `agent: developer`; `instruction: b. If yes: run it and validate` |
| **compute-prediction-accuracy** | **agent=workflow-improver** | inline | `scripts/inline/compute-prediction-accuracy.py` (Python — arithmetic) | **MISCLASSIFIED** — arithmetic on task counts + git diff; explicit formulas | `compute-prediction-accuracy.yaml` `agent: workflow-improver`; `instruction: 5. Compute accuracy metrics` |
| **archive-completed-change** | **agent=developer** | inline | `scripts/inline/archive-completed-change.sh` | **MISCLASSIFIED** — dir copy + `git commit`; all shell | `archive-completed-change.yaml` `agent: developer`; `instruction: 2. Create archive directory... 4. Commit` |
| remove-worktree | inline | inline | `scripts/inline/remove-worktree.sh` | `git worktree remove` + branch delete | `remove-worktree.yaml` (no agent field) |
| design-exploration | agent=architect | fold-into | design-and-draft-artifacts | Duplicates Part 1 of design-and-draft-artifacts | `design-and-draft-artifacts.yaml` Part 1 |
| create-or-refresh-artifacts | agent=architect | fold-into | design-and-draft-artifacts | Duplicates Part 2 of design-and-draft-artifacts | `design-and-draft-artifacts.yaml` Part 2 |
| run-implement-review | agent=reviewer | fold-into | run-phase-review | AC compliance + 5-dim scoring already in run-phase-review §5c | `run-phase-review.yaml` §5c |
| final-signoff | inline | fold-into | run-phase-review | Approval collection = terminal action of a reviewer pass | `final-signoff.yaml` `intent: Collect the final user approval` |
| validate-artifacts | inline | fold-into | design-and-draft-artifacts | Self-verification principle: absorbed into producing agent's `verify:` block | `design-and-draft-artifacts.yaml` `verify:` |
| phase-signoff | inline | fold-into | run-phase-review | **User decision #1**: approval-collection is the terminal action of the reviewer pass | `phase-signoff.yaml` `instruction: 0. Pre-check` |
| verify-spike-findings | inline | fold-into | run-phase-review (spike sub-rubric) | **User decision #2**: spike-specific sub-rubric inside reviewer, not a Python hook | `verify-spike-findings.yaml` `instruction: 3. Classify the recommendation` |

Data-row count: **31** (matches scope in-scope total).

### Misclassified-Math Steps

Three steps currently carry an `agent:` field but do deterministic work. Under
the rework plan (M3) the `agent:` field is removed, `inline: true` is added,
and the instruction prose is ported into a script file under
`$ORCHESTRATOR_HOME/scripts/inline/`. Shell by default; Python only where
shell hurts (YAML parsing, arithmetic-on-counts). The existing
`config/scripts/compute-swe-metrics.sh` is already shell and can move in
place.

| step_id | current `agent:` | why it's wrong | target inline script |
|---|---|---|---|
| compute-swe-metrics | developer | Instruction is "run `compute-swe-metrics.sh`, validate output". No LLM reasoning. | `scripts/inline/compute-swe-metrics.sh` |
| compute-prediction-accuracy | workflow-improver | Arithmetic on task counts + git diff line counts with explicit formulas (e.g. `rework_rate = fix_task_count / actual_tasks`). | `scripts/inline/compute-prediction-accuracy.py` (arithmetic grim in shell) |
| archive-completed-change | developer | Directory copy + `git commit` sequence; every instruction step is a shell command. | `scripts/inline/archive-completed-change.sh` |

The contract files stay at their current paths — only the `agent:` field is
removed and `inline: true` + `run: scripts/inline/<step-id>.{sh|py}` added;
the instruction prose is ported into the script body. The existing
`$ORCHESTRATOR_HOME/config/scripts/compute-swe-metrics.sh` moves to
`scripts/inline/compute-swe-metrics.sh` in place — no rewrite needed.

### Ambiguous Cases — Resolved

| ID | Case | Resolution | Decided by |
|---|---|---|---|
| AQ-1 | phase-signoff | Fold into `run-phase-review`. Reviewer owns scoring + approval collection. | User |
| AQ-2 | verify-spike-findings | Fold into `run-phase-review` as a spike-specific sub-rubric. No Python hook. | User |
| AQ-3 | Bootstrap scope | OUT OF SCOPE for HL-287. Separate follow-up stub below. | User |
| AQ-4 | generate-project-yaml / setup-makefile | Moot given AQ-3 (bootstrap out of scope). Re-evaluate in bootstrap follow-up. | User |
| OQ-4 | `ideator` → `designer` rename | Handled in M6 of the execution plan. | Architect |
| OQ-5 | `create-linear-ticket` flag-gate | Preserved as a flag check at the top of the inline script (shell `[[ "$LINEAR" == "true" ]] \|\| exit 0`). | Architect |

### Bootstrap Follow-Up Stub

The `bootstrap` schema has ~15 bootstrap-only step contracts (inline-candidate
setup + finalize steps, plus `generate-project-yaml` and `setup-makefile`
which have interactive user-review pauses). A separate ticket should decide
whether to apply the same `inline` / `agent-driven` split or leave bootstrap
as a distinct schema. **Out of scope for HL-287** — not addressed by any
milestone in the execution plan below.

---

## Rework — Execution Plan

> **Rev-2 amendment (post-merge prep)**: Five formerly-inline steps
> (`create-worktree`, `load-project-context`, `configure-gitignore`,
> `autopilot-session-init`, `create-linear-ticket`) collapsed into a single
> new agent step `workflow-init`. Reason: `create-linear-ticket` needs MCP
> Linear access, which only agents have. Consolidating the whole
> workflow-start sequence into one agent (vs. reaching back to the orchestrate
> skill between every step) matches the "one spawn, wide scope" principle.
>
> Net effect on the audit table below: 5 rows shift from category `inline` to
> category `agent-driven` (target_agent: workflow-init). The
> `scripts/inline/` directory shrinks from 13 to 8 scripts. All
> `workflow-init`-absorbed contract files are deleted; their workflow-schema
> references replaced with a single `- workflow-init` entry at the top of
> each schema's first phase.

### Sequencing Rationale

The plan interleaves what were previously framed as scope #2 (refactor) and
scope #3 (agent-role alignment). Both share one prerequisite — the typed
dispatcher foundation (M1) — and one final gate (M8). Between those, two
parallel lanes run:

- **Refactor lane** (M2 → M3 → M4 → M5): typed I/O on contracts, inline
  registry + misclassified-math port, fold-into deletions, skill-loop slim.
- **Role lane** (M6 → M7): agent-file rename/retire/relocate, then role-file
  rewrite to the 5-section template.

M6 depends on M1 only (needs typed dispatcher to resolve agent names cleanly).
It does **not** depend on M2 — renaming role files and their contract
references is mechanical and independent of the typed-I/O work. This lets the
role lane advance in parallel with M2/M3/M4/M5.

M7 depends on M6 (can only rewrite role files once the final roster is
settled). M8 is the cross-check gate: it cannot start until M3 + M5 + M7
complete.

```
             M1 (typed dispatcher)
             /                \
   refactor lane           role lane
   M2 (typed I/O)           M6 (rename/retire/relocate)
   |                        |
   M3 (inline + misc-math)  M7 (role-file rewrite)
   |                        |
   M4 (fold-into deletes)   |
   |                        |
   M5 (skill slim)          |
             \             /
              M8 (final gate: CI greps + golden-fixture)
```

### Milestones

#### M1 — Typed I/O in the Dispatcher Action Shape

- **ID**: M1
- **Goal**: Extend the existing `orchestrator next` dispatcher (already
  shipped by `subprocess-per-step-observability`) to carry typed
  `inputs` / `expected_outputs` in its action descriptor, and extend the
  `StepContract` dataclass to surface the contract's declared I/O. This is
  the foundation scope-2 and scope-3 both depend on — **not** a from-scratch
  CLI build.
- **Exit Criteria**:
  - `StepContract` in `config/scripts/orchestrator_next/parser.py` gains
    `inputs: list[str]` and `outputs: list[str]` fields, populated from the
    contract YAML when present (default `[]` when absent — backward
    compatible with contracts that haven't migrated yet).
  - `dispatch.py` action dict for `run_inline` / `run_step` / `retry_step`
    gains `inputs: {<name>: <resolved_value>}` and
    `expected_outputs: [<name>, ...]`. Values in `inputs` are resolved by
    threading outputs of prior terminal `step_history` entries through a
    simple name-match lookup (contract's declared `inputs:` names ↔ prior
    step's `evidence.outputs.<name>`).
  - Existing golden-fixture tests continue to pass byte-for-byte (goldens
    updated where new fields appear; additive only).
  - New test `test_typed_io.py` covers: (i) contract with declared inputs
    resolves from a prior step's evidence; (ii) missing input surfaces as a
    clear `blocked` action with a reason; (iii) contract with no declared
    inputs produces `inputs: {}` without error.
- **Dependencies**: none
- **Agent(s)**: developer
- **Concrete Changes**:
  - Edit `config/scripts/orchestrator_next/parser.py`: extend
    `StepContract` dataclass with `inputs` + `outputs`; update
    `load_contract_for_step` to populate them.
  - Edit `config/scripts/orchestrator_next/dispatch.py`: add
    `_resolve_inputs(state, contract)` helper; include `inputs` and
    `expected_outputs` in the `run_inline` / `run_step` / `retry_step`
    action dicts.
  - Add `config/scripts/tests/test_typed_io.py` plus fixtures under
    `config/scripts/tests/fixtures/` that declare `inputs:` / `outputs:` on
    a contract and a prior-step evidence block.
  - Regenerate existing goldens under `config/scripts/tests/golden/` where
    new keys appear (additive regeneration only).
  - No changes to `bin/orchestrator` CLI surface — `next` continues to
    work. `record` subcommand is deferred to **M5** (the skill can keep
    writing `step_history` inline until then; this keeps M1 scope small and
    independent).
- **Risk / Rollback**: Low — all changes are additive to existing code.
  Golden tests guard against unintended shape drift. Rollback: `git revert`.
- **Size**: S

#### M2 — Typed I/O on Surviving Step Contracts

- **ID**: M2
- **Goal**: Add explicit `inputs:` / `outputs:` field lists to every surviving
  step contract; the CLI validates on resolve.
- **Exit Criteria**:
  - Every step contract under `$ORCHESTRATOR_HOME/config/steps/` that survives
    the rework declares non-empty `inputs:` and `outputs:` lists.
  - `orchestrator next` rejects any contract missing either field.
  - CI grep: `! grep -L '^inputs:' config/steps/*.yaml` finds zero surviving
    contracts without the field.
- **Dependencies**: M1
- **Agent(s)**: developer (mechanical pass)
- **Concrete Changes**:
  - Edit every `$ORCHESTRATOR_HOME/config/steps/*.yaml` contract that will
    survive M4 to add `inputs:` and `outputs:` field lists. Reference shapes
    per the examples in this spec's Decisions section.
  - Extend `orchestrator next` validator to enforce presence.
  - Add CI grep to `$ORCHESTRATOR_HOME/Makefile` or equivalent lint target.
- **Risk / Rollback**: Field drift (typo in an input name threaded from a
  prior step). Mitigate via CI grep + a resolve-dry-run test on every schema.
  Rollback: `git revert`.
- **Size**: M

#### M3 — Inline Scripts Directory + Misclassified-Math Port

- **ID**: M3
- **Goal**: Create `$ORCHESTRATOR_HOME/scripts/inline/` as the home for
  inline step scripts (shell-first; Python only where shell hurts). Port the
  three misclassified-math steps as the first entries. Flip their contracts
  from agent-driven to inline.
- **Exit Criteria**:
  - `scripts/inline/` directory exists. Naming convention: one file per
    step_id — `scripts/inline/<step-id>.sh` (or `.py` if arithmetic / YAML
    parsing dominates). The directory itself is the lookup — no separate
    registry module.
  - Contract `run:` field points at the script path
    (e.g. `run: scripts/inline/create-worktree.sh`). Dispatcher semantics:
    contracts with `agent:` set are agent-driven — the skill spawns the
    named agent using `run:` as the adapter path (today's behaviour). New
    contracts with `inline: true` (and no `agent:` or `agent: inline`)
    point `run:` at the inline script — the skill (or a caller) executes
    the script directly with the action's `inputs` as env vars and parses
    the last stdout line as a JSON dict of outputs. Dispatcher distinguishes
    the two by presence of `inline: true`, returning action `run_inline`
    (execute directly) vs `run_step` (spawn agent). The existing
    `run_inline` action today means "no run field; skill executes
    instruction prose inline"; after M3 it gains the "run this script
    directly" variant when `run:` is present.
  - The three misclassified-math step_ids have `agent:` removed,
    `inline: true` + `run:` added; corresponding script files exist and
    pass their contract's `outputs:` validation.
  - The other ten inline step_ids from the audit table have script files
    created (shell for all except `load-project-context` which is Python —
    YAML merge is clumsy in shell). Porting their instruction prose is
    NOT required here; stub scripts that echo-then-fail are acceptable so
    M4/M5 can delete the folded contracts without losing step coverage.
  - End-to-end smoke test: `orchestrator next` on a feature schema runs the
    first inline step via its script and records outputs without spawning an
    agent.
- **Dependencies**: M2
- **Agent(s)**: developer
- **Concrete Changes**:
  - Create `$ORCHESTRATOR_HOME/scripts/inline/` with one script per
    step_id (convention: `<step-id>.sh` or `<step-id>.py`). Shell-first;
    Python for `load-project-context.py` and
    `compute-prediction-accuracy.py`. Move existing
    `config/scripts/compute-swe-metrics.sh` into `scripts/inline/`
    unchanged.
  - Standardize script I/O: inputs arrive as env vars (from the contract's
    `inputs:` list); outputs are a JSON dict on the last stdout line; exit
    non-zero on failure. Document this convention in a header comment on
    each script.
  - Edit the three misclassified-math contracts
    (`compute-swe-metrics.yaml`, `compute-prediction-accuracy.yaml`,
    `archive-completed-change.yaml`): remove `agent:`, add `inline: true`
    and `run: scripts/inline/<step-id>.{sh|py}`. Port the existing
    instruction prose into the script bodies.
  - Extend the `orchestrator` CLI to execute `run:` scripts for
    `inline: true` contracts and parse stdout JSON.
- **Risk / Rollback**: Low — the `compute-swe-metrics.sh` already exists
  and needs only relocation. `archive-completed-change` is pure shell
  (`cp -R` + `git commit`). Only `compute-prediction-accuracy` has
  arithmetic worth reaching for Python; the explicit formulas are short
  enough that the port is mechanical. Rollback: `git revert` — the
  original agent-driven contracts still work until M4 deletes folded
  siblings.
- **Size**: S

#### M4 — Fold-Into Deletions

- **ID**: M4
- **Goal**: Delete the 7 folded step contracts and absorb their verify logic
  into the absorbing siblings (`design-and-draft-artifacts`,
  `run-phase-review`). Update workflow schemas to remove folded step IDs.
- **Exit Criteria**:
  - The following contract files are deleted:
    `design-exploration.yaml`, `create-or-refresh-artifacts.yaml`,
    `validate-artifacts.yaml`, `run-implement-review.yaml`,
    `final-signoff.yaml`, `phase-signoff.yaml`, `verify-spike-findings.yaml`.
  - `design-and-draft-artifacts.yaml`'s `verify:` block absorbs
    `validate-artifacts` self-verification logic.
  - `run-phase-review.yaml` carries scoring + implement AC verification +
    approval collection + spike sub-rubric in its instruction prose.
  - `feature`, `bugfix`, and `spike` workflow schemas under
    `$ORCHESTRATOR_HOME/config/workflows/` no longer reference the deleted
    step IDs in any phase step-list.
  - Smoke test: a feature change runs specify → implement → complete through
    the updated schemas.
- **Dependencies**: M2, M3
- **Agent(s)**: developer
- **Concrete Changes**:
  - Delete the 7 files listed above under
    `$ORCHESTRATOR_HOME/config/steps/`.
  - Edit `$ORCHESTRATOR_HOME/config/steps/design-and-draft-artifacts.yaml`
    to absorb `validate-artifacts` verify logic.
  - Edit `$ORCHESTRATOR_HOME/config/steps/run-phase-review.yaml` to absorb
    `run-implement-review`, `final-signoff`, `phase-signoff`, and
    `verify-spike-findings` responsibilities as named sub-sections.
  - Edit `$ORCHESTRATOR_HOME/config/workflows/feature.yaml`,
    `bugfix.yaml`, `spike.yaml` to remove folded step IDs from phase
    step-lists.
- **Risk / Rollback**: Reversible via `git revert` of the delete commit —
  smoke-test before merge. Risk: a consumer outside the workflows references
  a deleted step_id; mitigate via grep before delete.
- **Size**: M

#### M5 — `orchestrator record` Subcommand + Slim the Orchestrate Skill

- **ID**: M5
- **Goal**: Add `orchestrator record` — a new CLI subcommand that accepts a
  completed step's outputs + usage + evidence, validates against the
  contract's `expected_outputs`, writes the step_history entry, and advances
  `next_step`. Then slim the `orchestrate` skill dispatch prose to a
  ~20-line loop that uses `next` + `record` as the only state-mutation path.
- **Exit Criteria**:
  - `bin/orchestrator record <state.yaml>` reads a JSON payload from stdin
    (`{step_id, phase, status, outputs, usage, evidence}`), validates that
    every key in `expected_outputs` (from the contract) is present in
    `outputs`, writes a terminal `step_history` entry with uniform
    `started_at` / `completed_at` / `usage`, and advances `next_step` to
    the next pending step per `workflow_plan`.
  - DuckDB `step_events` upsert fires on every `record` call, matching the
    existing behaviour today applied inside `next` for terminal entries.
  - Tests under `config/scripts/tests/test_orchestrator_record.py` cover
    (i) happy-path advance, (ii) validation failure on missing output,
    (iii) phase transition when current phase's active list is exhausted.
  - The `orchestrate` skill SKILL.md dispatch prose is ≤ ~25 lines: call
    `orchestrator next`; on `run_step`, spawn the named agent with action's
    `inputs` and expect `expected_outputs` back, then call `orchestrator
    record`; on `run_inline` with `run:` set, execute the script and call
    `record`; on `run_inline` without `run:` (legacy), continue to execute
    the inline instruction as today; on `complete_workflow` / `done`, stop.
  - One feature E2E smoke test completes specify → implement → complete
    using only `next` + `record` for state mutation.
- **Dependencies**: M4
- **Agent(s)**: developer (CLI) + architect (skill rewrite)
- **Concrete Changes**:
  - Add `record` case to `bin/orchestrator` arg parsing.
  - Add `config/scripts/orchestrator_next/record.py` implementing the
    write + advance logic. Shares `parser.py` / `upsert.py` with existing
    code.
  - Add `config/scripts/tests/test_orchestrator_record.py` with golden
    fixtures (before-state + JSON payload → after-state).
  - Rewrite `.claude/skills/orchestrate/SKILL.md` dispatch section to the
    ~20-line loop. Remove redundant state-management prose from
    neighbouring skills if any.
- **Risk / Rollback**: Medium — skill rewrite is the critical-path change.
  Mitigate by keeping the old skill file in git history and doing an E2E
  smoke test before merge. Rollback: `git revert` of the skill commit
  restores the previous dispatch prose.
- **Size**: M

#### M6 — Agent File Rename / Retire / Relocate

- **ID**: M6
- **Goal**: Rename misnamed agent files, retire unused ones, relocate
  model-tier files, and update every step-contract `agent:` field that
  references them.
- **Exit Criteria**:
  - `$ORCHESTRATOR_HOME/agents/ideator.md` is renamed to `designer.md`.
  - `$ORCHESTRATOR_HOME/agents/workflow-improver.md` is renamed to
    `learner.md`.
  - `$ORCHESTRATOR_HOME/agents/debugger.md` and
    `$ORCHESTRATOR_HOME/agents/humanizer.md` are retired — moved to
    `$ORCHESTRATOR_HOME/agents/archive/` with a short ADR noting the SHA
    before removal.
  - `$ORCHESTRATOR_HOME/agents/haiku-agent.md` and
    `$ORCHESTRATOR_HOME/agents/sonnet-agent.md` are relocated under
    `$ORCHESTRATOR_HOME/config/models/` (or equivalent models/tiers dir).
  - `$ORCHESTRATOR_HOME/config/steps/ux-design.yaml` references
    `agent: designer`.
  - `$ORCHESTRATOR_HOME/config/steps/run-learn-cycle.yaml` references
    `agent: learner`.
  - CI grep: zero references to `ideator`, `workflow-improver`, `debugger`,
    or `humanizer` under `config/` and `agents/` (outside `archive/`).
  - `$ORCHESTRATOR_HOME/agents/` contains exactly 7 role files (or 6 if
    `ux-reviewer` collapses into `reviewer` — decision deferred to M7 spec
    pass).
- **Dependencies**: M1 (needs typed dispatcher to resolve agent names
  cleanly; can run in parallel with M2–M5)
- **Agent(s)**: developer
- **Concrete Changes**:
  - Rename `agents/ideator.md` → `agents/designer.md`;
    `agents/workflow-improver.md` → `agents/learner.md`.
  - Move `agents/debugger.md`, `agents/humanizer.md` → `agents/archive/`.
  - Move `agents/haiku-agent.md`, `agents/sonnet-agent.md` →
    `config/models/` (or equivalent).
  - Edit `config/steps/ux-design.yaml`: `agent: ideator` → `agent: designer`.
  - Edit `config/steps/run-learn-cycle.yaml`:
    `agent: workflow-improver` → `agent: learner`.
  - Add CI grep to the lint target for retired/renamed identifiers.
- **Risk / Rollback**: Low — all mechanical. Rollback: `git revert`.
- **Size**: S

#### M7 — Role-File Rewrite to 5-Section Template

- **ID**: M7
- **Goal**: Rewrite every surviving role file to the Role Definition Template
  (5 named sections). Add a lint check that enforces section presence.
- **Exit Criteria**:
  - Every role file under `$ORCHESTRATOR_HOME/agents/` contains the five
    sections: **Purpose**, **Philosophy**, **Responsibilities**,
    **Constraints**, **Evidence standards**.
  - No role file declares machine-readable `inputs:` / `outputs:` /
    `reads:` / `produces:` blocks — enforced by CI grep.
  - The `reviewer.md` role explicitly documents the four responsibilities
    absorbed by M4: phase review scoring, implement AC verification,
    approval collection, spike verdict rubric.
  - Lint check (`orchestrator lint-roles` or equivalent) validates section
    presence; CI target runs it.
- **Dependencies**: M6
- **Agent(s)**: architect
- **Concrete Changes**:
  - Rewrite `agents/discoverer.md`, `agents/architect.md`,
    `agents/developer.md`, `agents/reviewer.md`, `agents/ux-reviewer.md`,
    `agents/designer.md`, `agents/learner.md` to the 5-section template.
  - Add a lint module under
    `$ORCHESTRATOR_HOME/src/orchestrator/lint_roles.py`.
  - Add a CI target invoking the lint.

##### Role Definition Template

Each role file describes **the kind of reasoning the agent does** — not the
specific I/O shape of any single invocation. Required sections:

- **Purpose** — one-line statement (e.g. "Discover what already exists and
  decide whether to build, reuse, or extend").
- **Philosophy** — reasoning style / principles (e.g. "default to don't
  build", "verify claims with evidence").
- **Responsibilities** — kinds of tasks this role handles, expressed as
  capabilities (not fixed I/O shapes).
- **Constraints** — what the role must or must not do.
- **Evidence standards** — what counts as valid justification for claims.

**Interaction with step contracts** (note required by AC-10): a role
definition does **NOT** declare typed inputs or outputs. Those are declared
per-step on the step contract that invokes the role (M2). The same role may
be invoked with different I/O shapes across workflows — e.g., `developer`
takes `{task_id, test_path}` in one step and `{acceptance_criterion,
spec_ref}` in another. The role brings its philosophy and evidence standards
to whichever shape the step hands it. Role files therefore do not carry a
machine-readable I/O block.

##### Consolidation Map (executed across M6 + M7)

| Current agent file | Action | Milestone |
|---|---|---|
| `discoverer.md` | Keep — rewrite to template | M7 |
| `architect.md` | Keep — rewrite to template | M7 |
| `developer.md` | Keep — rewrite to template | M7 |
| `reviewer.md` | Keep — rewrite to template; document four absorbed responsibilities | M7 |
| `ux-reviewer.md` | Keep — rewrite to template (collapse into `reviewer` deferred — see note) | M7 |
| `ideator.md` | Rename → `designer.md`; rewrite to template | M6 (rename) + M7 (rewrite) |
| `workflow-improver.md` | Rename → `learner.md`; rewrite to template | M6 (rename) + M7 (rewrite) |
| `debugger.md` | Retire → `agents/archive/` | M6 |
| `humanizer.md` | Retire → `agents/archive/` | M6 |
| `haiku-agent.md` | Relocate → `config/models/` | M6 |
| `sonnet-agent.md` | Relocate → `config/models/` | M6 |

*Note*: keeping `ux-reviewer` separate is the default; a collapse into
`reviewer` is deferred as a strict-6-roles option.

- **Risk / Rollback**: Medium — content rewrites risk information loss.
  Mitigate by preserving the pre-rewrite files in git history; each rewrite
  is a single commit per file. Rollback: `git revert` per file.
- **Size**: M

#### M8 — Final Gate: CI Greps + Golden-Fixture Diff

- **ID**: M8
- **Goal**: Cross-check every invariant from the prior milestones end-to-end.
  Final acceptance gate for the rework.
- **Exit Criteria**:
  - A dry-run of each workflow schema (feature, bugfix, spike) successfully
    resolves all `agent:` references against the new role roster.
  - CI grep confirms no `hooks/` subsystem exists.
  - CI grep confirms no role file declares typed I/O blocks.
  - CI grep confirms zero references to retired agent names (`ideator`,
    `workflow-improver`, `debugger`, `humanizer`) outside `agents/archive/`.
  - Golden-fixture diff: running a full feature E2E produces a
    `state.yaml` whose `step_history` shape matches the golden baseline from
    M1.
  - `orchestrator lint-roles` passes on every role file.
- **Dependencies**: M3, M5, M7
- **Agent(s)**: reviewer
- **Concrete Changes**:
  - Add or extend `$ORCHESTRATOR_HOME/Makefile` targets for the three CI
    greps and the schema-resolve dry-run.
  - Add a golden-fixture diff test under
    `$ORCHESTRATOR_HOME/tests/test_e2e_fixture.py`.
- **Risk / Rollback**: Low — this is a check, not a mutation. If a check
  fails, the preceding milestone is reopened. No rollback needed for M8
  itself.
- **Size**: S

### Sizing Summary

| Milestone | Size |
|---|---|
| M1 | S |
| M2 | M |
| M3 | M |
| M4 | M |
| M5 | M |
| M6 | S |
| M7 | M |
| M8 | S |

Rough person-weeks: M≈1, S≈0.5 → **~5 person-weeks** upper-bound,
tightened by the parallel lanes (M6/M7 alongside M2–M5) to an estimated
**~3–4 person-weeks** calendar. Sizing was re-grounded after reading the
existing `config/scripts/orchestrator_next/` code shipped by
`subprocess-per-step-observability`: M1 shrank (dispatcher already exists —
M1 only adds typed I/O to its action shape and extends `StepContract`);
M3 and M5 grew (inline-script execution semantics and `orchestrator record`
subcommand are new work the prior plan had under-specified).
