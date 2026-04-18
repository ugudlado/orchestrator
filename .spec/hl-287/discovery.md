---
feature-id: hl-287
linear-ticket: HL-287
---

# Discovery Brief: HL-287 Orchestrator-Worker Rework — Step Audit

## Feature Summary

The current orchestrator dispatches against ~45 named step contracts. Many of those contracts
encode deterministic bookkeeping (file copies, metric scripts, git worktree ops) but run under
a full LLM agent spawn because the `agent:` field is present, or because the instructions are
long and invoke tools rather than just shell scripts. The rework collapses the dispatch surface
to ~10 cognitive agent steps plus Python init/finalize hooks, a few dispatcher primitives, and
the folds/drops described below. This document audits all 45 current step contracts, assigns
each to a target category, and surfaces ambiguous cases for architect judgment.

This is scope item #1 (Audit only). Scopes #2 (Refactor) and #3 (Agent-role alignment) are
follow-up tickets.

## Personas & Actors

- Architect (consumer of this brief): reads the per-step categorization table and decides
  which ambiguous cases to resolve and how.
- Orchestrator dispatcher (system actor): currently iterates the 45-step list; under the
  rework it will call init-hooks, dispatch to agents, then call finalize-hooks.
- Workflow agent roles (producers): discoverer, architect, developer, reviewer,
  ux-reviewer, designer, workflow-improver, learner — the 6-8 target roles.

## Use Cases

### Happy Path

UC-1: Architect consumes discovery brief — the architect reads the categorization table,
selects the target approach, and proceeds to design the new contract shape with full
knowledge of which steps fold, which become hooks, and which stay agent-driven.

UC-2: Dispatcher refactor uses table as canonical mapping — the developer implementing
scope #2 uses the table's `target_category` column as the authoritative migration list,
with `evidence` fields linking to exact contract lines for each decision.

### Error & Edge Cases

UC-E1: Step does not fit any target category — `verify-spike-findings` is schema-specific
(spike workflow only) and encodes neither mechanical bookkeeping nor open-ended cognitive
work; it is a deterministic structural assertion. This brief flags it as ambiguous (see
Open Questions) rather than silently mapping it to a wrong category.

## Scope

### In Scope

- Audit all 45 step contracts in `$ORCHESTRATOR_HOME/steps/` (no config/steps overrides differ
  from base — confirmed via md5 comparison).
- Assign each step one of: init-hook, finalize-hook, agent-driven, dispatcher-primitive,
  fold-into, ambiguous.
- Identify agent-field vs inline split (14 vs 31).
- Flag which agent-field steps should become Python hooks (deterministic math).
- Identify reviewer/validation overlap.
- Shallow state.yaml integration pass (fields read/written, not exhaustive).

### Out of Scope

- Python file layout / hook implementation design — architect scope (#2).
- Agent-role contract redesign — architect scope (#3).
- External precedents — already surveyed (Dagster Pipes, Stepwise).
- Bootstrap and spike workflow restructuring beyond categorization.

## UI Direction

N/A — no UI components.

## Key Decisions

### Selected Proposal-Document Approach (architect, 2026-04-18)

Single-table proposal (Approach A of three considered: single-table / narrative-per-category / hybrid).
Auto-selection heuristic: lowest complexity. User decisions made during discovery pre-resolved the
contentious cases that a hybrid Discussion section would have hosted, collapsing hybrid into
single-table. Narrative-per-category loses on consumability for scope #2, which needs the table
as a migration mapping. See `design.md` Approaches Considered for full pros/cons.

### Per-Step Categorization Table

**Column definitions:**
- `current_mode`: `agent=<name>` if step has an `agent:` field; `inline` otherwise.
- `target_category`: one of init-hook | finalize-hook | agent-driven | dispatcher-primitive | fold-into | ambiguous.
- `target_role_or_hook`: agent role (6 canonical) or hook name, or sibling step being folded into.
- `rationale`: one-line consolidation reason.
- `evidence`: contract file field or line reference (all paths relative to `$ORCHESTRATOR_HOME/steps/`).

#### Agent-driven steps (cognitive work dispatched to a named role)

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| explore | agent=discoverer | agent-driven | discoverer | Core discovery cognitive work — surveys codebase, writes discovery.md | explore.yaml `agent: discoverer` |
| diagnose | agent=discoverer | agent-driven | discoverer | Bug-specific discovery — root-cause tracing requires LLM reasoning | diagnose.yaml `agent: discoverer` |
| design-and-draft-artifacts | agent=architect | agent-driven | architect | Primary design cognitive step; replaces design-exploration + create-or-refresh-artifacts | design-and-draft-artifacts.yaml `agent: architect` |
| execute-next-task | agent=developer | agent-driven | developer | Core implementation loop — requires reasoning per task, retry logic | execute-next-task.yaml `agent: developer` |
| run-phase-review | agent=reviewer | agent-driven | reviewer | Quality gate review — scores dimensions, emits fix tasks; covers run-implement-review | run-phase-review.yaml `agent: reviewer` |
| run-ux-critique | agent=ux-reviewer | agent-driven | ux-reviewer | UI quality gate — conditional on ux_design flag | run-ux-critique.yaml `agent: ux-reviewer` |
| ux-design | agent=ideator | agent-driven | designer | UI prototyping — currently mapped to ideator but target role is designer (role mismatch) | ux-design.yaml `agent: ideator` |
| run-learn-cycle | agent=workflow-improver | agent-driven | learner | Learning trigger — invokes /learn skill, requires evaluator reasoning | run-learn-cycle.yaml `agent: workflow-improver` |

#### Dispatcher primitives (autopilot loop — not in step list under target shape)

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| autopilot-iterate | inline | dispatcher-primitive | autopilot-iterate | Controls the autopilot loop; is the loop body, not a workflow step | autopilot-iterate.yaml (no agent field) |
| autopilot-preflight | inline | dispatcher-primitive | autopilot-preflight | Pre-flight checks for autonomous execution; dispatcher concern | autopilot-preflight.yaml (no agent field) |
| autopilot-session-report | inline | dispatcher-primitive | autopilot-session-report | Session summary printer; dispatcher concern, no agent spawn | autopilot-session-report.yaml (no agent field) |

#### Init hooks (mechanical bookkeeping at workflow/phase start; Python, no LLM)

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| create-worktree | inline | init-hook | create-worktree | Pure shell: git worktree add + symlink .env + install deps; no reasoning | create-worktree.yaml `instruction:` — all bash commands |
| load-project-context | inline | init-hook | load-project-context | Reads project.yaml + schema, computes workflow_plan; deterministic merge | load-project-context.yaml `instruction:` — file reads + YAML merge |
| configure-gitignore | inline | init-hook | configure-gitignore | Appends entries to .gitignore; deterministic per language matrix | configure-gitignore.yaml `instruction:` — append-only file ops |
| check-bootstrap-state | inline | init-hook | check-bootstrap-state | Reads .tooling-state.json, gates bootstrap continuation; purely conditional | check-bootstrap-state.yaml `instruction:` — read + compare |
| capture-test-baseline | inline | init-hook | capture-test-baseline | Runs test command, parses counts, writes `baseline:` block to state.yaml | capture-test-baseline.yaml `instruction:` — run + parse + write |
| preview-route | inline | init-hook | preview-route | Runs estimate-cost.sh, appends route_preview block; deterministic script call | preview-route.yaml `instruction: 1. Run the estimator: $ORCHESTRATOR_HOME/config/scripts/estimate-cost.sh` |
| autopilot-session-init | inline | init-hook | autopilot-session-init | Creates sessions.yaml entry + _checkpoint.json; pure state initialization | autopilot-session-init.yaml (no agent field) |
| create-linear-ticket | inline | init-hook | create-linear-ticket | API call to create Linear issue; deterministic given config inputs | create-linear-ticket.yaml (no agent field); ticket explicitly names this as an init hook |

#### Finalize hooks (mechanical bookkeeping at phase/workflow completion; Python, no LLM)

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| mark-change-completed | inline | finalize-hook | mark-change-completed | Writes `status: completed`, `completed_at`, `archive_path` to state.yaml; deterministic | mark-change-completed.yaml comment: "Inline step — no agent field" |
| compute-swe-metrics | agent=developer | finalize-hook | compute-swe-metrics | Runs compute-swe-metrics.sh; deterministic math — agent field is wrong | compute-swe-metrics.yaml `agent: developer`; `instruction:` invokes shell script |
| compute-prediction-accuracy | agent=workflow-improver | finalize-hook | compute-prediction-accuracy | Arithmetic on task counts + git diff; deterministic — agent field is wrong | compute-prediction-accuracy.yaml `agent: workflow-improver`; no LLM reasoning in instruction |
| archive-completed-change | agent=developer | finalize-hook | archive-completed-change | File copy + git commit; no reasoning — agent field is wrong | archive-completed-change.yaml `agent: developer`; instruction is directory copy sequence |
| remove-worktree | inline | finalize-hook | remove-worktree | git worktree remove + branch delete; pure shell | remove-worktree.yaml (no agent field) |

#### Fold-into (redundant with another step — collapsed under target shape)

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| design-exploration | agent=architect | fold-into | design-and-draft-artifacts | Ticket lists design-and-draft-artifacts as the sole architect step; design-exploration is Part 1 of that step's instruction (generate + select approaches) | design-and-draft-artifacts.yaml `instruction: ## Part 1: Design Selection`; design-exploration.yaml `intent: Generate solution-space design approaches` |
| create-or-refresh-artifacts | agent=architect | fold-into | design-and-draft-artifacts | Artifact generation + tasks; fully duplicated by design-and-draft-artifacts Part 2 | create-or-refresh-artifacts.yaml vs design-and-draft-artifacts.yaml `## Part 2: Artifact Generation` |
| run-implement-review | agent=reviewer | fold-into | run-phase-review | Ticket says "run-implement-review likely merges with run-phase-review"; AC compliance + 5-dimension scoring already in run-phase-review (§5c) | run-implement-review.yaml `intent`; run-phase-review.yaml `5c. AC verification with evidence (implement phase only)` |
| final-signoff | inline | fold-into | run-phase-review | Ticket says "final-signoff likely folds into run-phase-review"; approval collection is the last step of a reviewer pass | final-signoff.yaml `intent: Collect the final user approval`; run-phase-review.yaml pattern |
| validate-artifacts | inline | fold-into | design-and-draft-artifacts | Ticket: "Validation: absorbed by the producing agent per self-verification principle" | validate-artifacts.yaml `intent`; design-and-draft-artifacts.yaml `verify:` block |
| phase-signoff | inline | ambiguous | — | See Ambiguous section below | — |

#### Bootstrap-only steps (separate workflow — categorization applies to bootstrap schema)

Bootstrap steps belong to the `bootstrap` schema, not the feature/bugfix lifecycle the ticket targets.
The target shape's init/finalize hook model applies if bootstrap is similarly restructured; if not,
these remain inline steps in a separate schema. Flagged as an Open Question.

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| git-init | inline | init-hook (bootstrap) | git-init | Pure git init check; deterministic | git-init.yaml `instruction:` |
| detect-language | inline | init-hook (bootstrap) | detect-language | File presence detection; deterministic | detect-language.yaml `instruction:` |
| install-tooling | inline | init-hook (bootstrap) | install-tooling | Package installs per language matrix; scripted | install-tooling.yaml |
| generate-project-yaml | inline | init-hook (bootstrap) | generate-project-yaml | Template selection + user Q&A; has interactive pause — possibly agent-driven | generate-project-yaml.yaml `instruction: 4. Present the suggestion to the user` |
| configure-gitignore | inline | init-hook (bootstrap) | configure-gitignore | Already categorized above (shared with feature lifecycle) | — |
| setup-claude-md | inline | init-hook (bootstrap) | setup-claude-md | Creates CLAUDE.md + AGENTS.md pointers; deterministic | setup-claude-md.yaml |
| setup-claude-settings | inline | init-hook (bootstrap) | setup-claude-settings | Writes .claude/settings.json; deterministic | setup-claude-settings.yaml |
| setup-makefile | inline | init-hook (bootstrap) | setup-makefile | Creates/updates Makefile; has user review pause — boundary case | setup-makefile.yaml `instruction: 2. Present the Makefile to the user` |
| setup-portless | inline | init-hook (bootstrap) | setup-portless | Invokes /portless skill; conditional, scripted | setup-portless.yaml |
| run-quality-baseline | inline | init-hook (bootstrap) | run-quality-baseline | Runs quality gates + auto-fix; scripted | run-quality-baseline.yaml |
| check-linear-config | inline | init-hook (bootstrap) | check-linear-config | Reads ~/.config/linear/config.yaml; informational | check-linear-config.yaml `rules: Never block bootstrap on Linear config` |
| register-with-orchestrator-home | inline | init-hook (bootstrap) | register-with-orchestrator-home | Runs register-repo.sh; non-blocking script | register-with-orchestrator-home.yaml `non_blocking: true` |
| bootstrap-commit | inline | finalize-hook (bootstrap) | bootstrap-commit | git add + commit; deterministic | bootstrap-commit.yaml |
| write-bootstrap-state | inline | finalize-hook (bootstrap) | write-bootstrap-state | Writes .tooling-state.json + state.yaml; deterministic | write-bootstrap-state.yaml |
| verify-report | inline | finalize-hook (bootstrap) | verify-report | Prints bootstrap summary; stdout-only | verify-report.yaml |

#### Spike-only steps

| step_id | current_mode | target_category | target_role_or_hook | rationale | evidence |
|---------|-------------|----------------|--------------------|-----------|-----------------------------|
| verify-spike-findings | inline | ambiguous | — | Structural assertion on spike artifacts (question answered? verdict classifiable?); no LLM spawn, but schema-specific. Could be finalize-hook or fold into reviewer. | verify-spike-findings.yaml `instruction:` — deterministic pattern matching; no `agent:` |

### Agent-Field vs Inline Split

- Steps with `agent:` field: **14**
  - discoverer: explore, diagnose
  - architect: create-or-refresh-artifacts, design-and-draft-artifacts, design-exploration
  - developer: archive-completed-change, compute-swe-metrics, execute-next-task
  - reviewer: run-implement-review, run-phase-review
  - workflow-improver: compute-prediction-accuracy, run-learn-cycle
  - ux-reviewer: run-ux-critique
  - ideator: ux-design
- Steps without `agent:` field (inline): **31**

### Steps with Agent Field Whose Instructions Are Deterministic Math (Should Become Python Hooks)

Three steps have an `agent:` field but contain no open-ended LLM reasoning — their instructions
are deterministic math or scripted file operations:

1. `compute-swe-metrics` (`agent: developer`) — invokes `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh`; the step instruction is "run the script, catch errors". Evidence: compute-swe-metrics.yaml `instruction: b. If yes: run it and validate the output`.
2. `compute-prediction-accuracy` (`agent: workflow-improver`) — arithmetic on task counts and git diff line counts with explicit formulas (e.g., `rework_rate = fix_task_count / actual_tasks`). Evidence: compute-prediction-accuracy.yaml `instruction: 5. Compute accuracy metrics`.
3. `archive-completed-change` (`agent: developer`) — directory copy + `git commit`; all steps are shell commands. Evidence: archive-completed-change.yaml `instruction: 2. Create archive directory... 3. Copy... 4. Commit... 5. Clean up`.

### Reviewer/Validation Overlap

- `run-phase-review` covers both specify and implement phases (instructions include `5c. AC verification with evidence (implement phase only)`).
- `run-implement-review` duplicates the AC compliance + 5-dimension scoring already present in `run-phase-review` — confirmed by identical scoring rubric in both contracts. Target: fold into run-phase-review.
- `final-signoff` collects approval after `run-phase-review` has already recorded findings; the approval collection can be the terminal action of the reviewer step. Target: fold into run-phase-review (or make it a thin inline step — see Open Questions).
- `validate-artifacts` performs structural artifact checks; under self-verification principle this becomes part of each producing agent's `verify:` block.
- `phase-signoff` also collects approval but with a pre-check requirement that `run-phase-review` completed. Ambiguous: does it merge with `final-signoff` → fold into run-phase-review, or is the approval-collection interaction concern separate from review scoring?

### State.yaml Integration Points (Shallow Pass)

| step_id | reads from state.yaml | writes to state.yaml |
|---------|----------------------|---------------------|
| load-project-context | `schema`, `flags`, `phase` | `workflow_plan`, `project_context_loaded`, `step_history[-1]` |
| create-worktree | `slug` | `worktree_path`, `step_history[-1]` |
| preview-route | `change_id` | `route_preview:`, `step_history[-1]` |
| capture-test-baseline | — | `baseline:`, `step_history[-1]` |
| execute-next-task | `task_checkpoint`, `baseline` | `task_checkpoint`, `step_history[-1]`, `retries.T-N`, `quarantine_events` |
| run-phase-review | `quarantine_events` | `step_history[-1].review_score`, `retries.<step_id>` |
| mark-change-completed | `change_id`, `completed_at` | `status`, `completed_at`, `archive_path`, `step_history[-1]` |
| compute-swe-metrics | — | `metrics:`, `step_history[-1]` |
| compute-prediction-accuracy | `feature_id`, branch info | `prediction_accuracy:`, `step_history[-1]` |
| archive-completed-change | `swe_metrics`, storage config | `step_history[-1]` |
| create-linear-ticket | — | `linear_ticket_id`, `step_history[-1]` |
| autopilot-session-init | — | sessions.yaml entry, `_checkpoint.json` |
| autopilot-iterate | `_checkpoint.json`, sessions.yaml | `_checkpoint.json`, sessions.yaml, iteration record |

## Ambiguous Cases (Needs Human Judgment)

**AQ-1: `phase-signoff`** — This step requires `run-phase-review` to have completed (pre-check
in instruction step 0), then collects explicit user approval. Under the target shape, `final-signoff`
is listed as folding into `run-phase-review`. But `phase-signoff` and `final-signoff` serve the same
approval-collection concern with slightly different scope (phase boundary vs workflow end). Trade-off:
merging both into `run-phase-review` makes the reviewer step responsible for both review scoring AND
approval gating, which is a larger cognitive surface. Keeping approval as a thin inline step is simpler
but leaves one more step in the list. Evidence: phase-signoff.yaml `instruction: 0. Pre-check: verify
run-phase-review completed` vs final-signoff.yaml `intent: Collect the final user approval`.

**AQ-2: `verify-spike-findings`** — Pure structural assertion (does the spike brief have a non-empty
`## Question` section? does `## Recommendations` contain a classifiable verdict?). No LLM spawn, no
`agent:` field. Could be a finalize-hook for the spike schema (deterministic) or could fold into
`run-phase-review` with a spike-specific rubric. Trade-off: a Python finalize-hook is simpler but
must be schema-aware (only runs in spike workflow); folding into reviewer keeps the step-list uniform
but requires the reviewer to handle spike-specific artifact format. Evidence: verify-spike-findings.yaml
`instruction: 3. Classify the recommendation. Scan for verdict line...` — fully deterministic regex match.

**AQ-3: Bootstrap scope of the rework** — 16 of the 45 steps belong exclusively or primarily to the
`bootstrap` schema. The ticket's target shape describes the feature/bugfix lifecycle. It is not stated
whether the rework applies to bootstrap as well. If yes, steps like `install-tooling`,
`generate-project-yaml`, `setup-makefile` (which have interactive user-review pauses) are borderline
agent-driven vs init-hook. If no, all bootstrap steps remain inline and are out of scope for HL-287's
#2 and #3. Evidence: state.yaml workflow_plan shows bootstrap steps absent from the feature lifecycle
`active:` list; bootstrap steps reference `check-bootstrap-state.yaml intent: Check if bootstrap
already completed` which is structurally separate.

**AQ-4: `generate-project-yaml` and `setup-makefile`** — Both have interactive "present to user for
review / incorporate feedback" pauses in their instructions. Under a strict "Python hook = no LLM"
rule, these cannot be hooks. But their interactive nature is a setup-time concern, not ongoing
cognitive work. If bootstrap stays inline, this is moot. If bootstrap gets the same treatment, these
two need a fourth category (interactive-setup) or must be treated as agent-driven. Evidence:
generate-project-yaml.yaml `instruction: 7. Present the filled template to the user`; setup-makefile.yaml
`instruction: 2. Present the Makefile to the user`.

## Open Questions

- OQ-1: Does the target shape (init-hooks + agents + finalize-hooks) apply to the `bootstrap` schema,
  or only to feature/bugfix/spike lifecycle schemas? The audit categorizes bootstrap steps provisionally
  as init-hook (bootstrap) / finalize-hook (bootstrap) but the architect must decide scope.

- OQ-2: `phase-signoff` vs `final-signoff` — are these merged into `run-phase-review` (making reviewer
  responsible for scoring + approval), or is approval-collection kept as a separate thin inline step?

- OQ-3: `verify-spike-findings` — finalize-hook (Python deterministic) or fold into `run-phase-review`
  with a spike-specific rubric?

- OQ-4: The `ux-design` step currently has `agent: ideator`; the target roles list `designer` as the
  correct role. Is the rename automatic in the refactor, or is there a separate agent definition to
  create?

- OQ-5: `create-linear-ticket` is listed in the ticket as an init-hook. The current contract has no
  `agent:` field (inline). Confirm: the "flag-gated" behavior (`create-linear-ticket if linear`) is
  preserved as a Python conditional in the hook runner, not dropped.

