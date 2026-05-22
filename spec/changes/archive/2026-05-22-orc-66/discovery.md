---
feature-id: ORC-66
linear-ticket: none
---

# Discovery Brief: Extract deterministic side-effects into run: nodes; classify every step by judgment vs determinism

## Feature Summary

The orchestrator's `execute-next-task` agent step today bundles two conceptually distinct
lanes: LLM judgment (read code, decide what to write, implement) and deterministic side
effects (mark tasks complete, commit, stash, compute regression counts). This ticket
establishes and enforces the organizing principle — LLM where judgment is required,
script where the work is determined by state — by peeling every deterministic side effect
out of agent spawns and into explicit `run:` nodes wired with `depends_on`. It also
records the principle in two durable places (`project.yaml` workflow rules and
`config/steps/CONVENTIONS.md`) so it propagates into every future plan via rule-merge.

## Personas & Actors

- **Workflow author** — defines steps in YAML; needs a clear decision rule for when to
  use `agent:` vs `run:`.
- **Developer agent** — spawned by `execute-next-task`; currently performs git commits,
  task-file marking, and regression comparison; under the new model it produces declared
  outputs only.
- **Orchestrator dispatcher** (`dispatch.py`) — routes steps to the correct lane; already
  supports both `agent:` and `run:` paths (ORC-45 two-path dispatch); no new dispatch
  logic is required.
- **run: script** — a deterministic shell script that consumes agent-declared outputs and
  performs a single side effect idempotently.

## Use Cases

### Happy Path

UC-1: Per-task commit via run: node — the developer agent implements a task and emits its
commit message as a declared output; a downstream `commit-task[run]` node executes
`git commit` with that message, so the LLM never touches git directly.

UC-2: Task completion marking via run: node — after the commit node succeeds, a
`mark-task-done[run]` node flips the tasks.md checkbox from `[ ]` to `[x]`, making the
checkpoint durable without requiring an agent to self-edit the tracking file.

UC-3: step-classification rule in project.yaml — a workflow-improver running `/learn`
picks up the litmus-test rule from `project.yaml rules:` via rule-merge and emits it as
a step-level constraint on any newly created agent step; the principle propagates
automatically.

### Error & Edge Cases

UC-E1: Agent produces malformed commit message output — the `commit-task[run]` script
receives a missing or empty `commit_message` input; the script exits non-zero; the
dispatcher marks the step blocked; the agent is re-dispatched to re-emit the output (not
to re-implement the task).

UC-E2: Regression check script cannot parse test output — `regression-check[run]` exits
with `regression_check.skipped: true` in its payload per existing contract; the agent
downstream receives this signal and continues without a numeric gate, matching the current
fallback behavior in execute-next-task.yaml line 84.

UC-E3: Classification audit finds a step that bundles judgment with side effects — no
existing workflow matches detect it at dispatch time; the issue surfaces only at review.
The CONVENTIONS.md litmus-test section gives reviewers and workflow-improver a documented
criterion to catch future bundling before it reaches main.

## Scope

### In Scope

- Audit of `execute-next-task.yaml` and the `developer`/`reviewer`/`linear` skills to
  identify deterministic side effects embedded in agent work.
- Extraction of at minimum: per-task `git commit` invocation, tasks.md `[x]` marking,
  and `git stash push/pop/drop` (rollback) into `run:` nodes.
- Classification of every existing step contract (agent: or run:) against the litmus test.
  Any step that bundles judgment with deterministic side effects is flagged as a split
  candidate.
- Addition of a named rule to `project.yaml rules:` stating the LLM-vs-script
  classification principle, so rule-merge propagates it into every plan.
- Addition of a `§ Step Classification` section to `config/steps/CONVENTIONS.md` with
  the litmus test as the step-authoring decision procedure.
- Where an agent must produce content for a deterministic step (e.g. commit message,
  final-summary text), the agent declares it as an output and the run: node declares it
  as an input — wired via `depends_on`.

### Out of Scope

- Changing `dispatch.py` or `record.py` dispatch logic — the ORC-45 two-lane model
  already supports both `agent:` and `run:`; no engine change is needed.
- Modifying the ORC-65 per-task DAG node model — ORC-66 wires side-effect nodes
  between task nodes, but does not define how the task graph is expanded; that is ORC-65.
- Ticket status transitions driven by the `developer` or `reviewer` skills — those
  skills already delegate ticket operations to `/backlog-manager` outside the agent
  spawn (see SKILL.md evidence below); they satisfy AC-3 today.
- Regression analysis decision-making (deciding if a regression is acceptable) — that
  remains agent judgment; only the deterministic count comparison moves to a run: node.
- Rewriting inline shell scripts that are already correctly in `run:` nodes — they are
  the pattern to follow, not candidates for change.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Build or reuse?** The ORC-45 two-path dispatch model (`dispatch.py:326-358`) already
  provides both lanes. No engine changes are required. This ticket reuses the existing
  `run:` lane more aggressively — the build cost is authoring new step contracts and
  scripts, not modifying the dispatcher.

- **Litmus test as the classification criterion**: "If a script given this exact input
  could produce the right output every time → run:. If it needs to weigh/interpret/
  generate → agent:." Burden of proof is on agent:. This matches the existing
  `CONVENTIONS.md § Single Responsibility Principle` intent but makes it operational
  with an explicit decision procedure.

### Architect decision (design-and-draft-artifacts, final revision 2026-05-22)

**Model — final, supersedes all earlier revisions.** Earlier revisions explored
per-task `run:` nodes (forward-declared, then a persisted per-task node graph,
then an ephemeral one). The user has settled on a **simpler model** that
supersedes all of them: **one developer agent spawn per task; the agent owns the
complete unit of work (implement, verify, commit, mark its task `[x]`); the
driver does pure orchestration; a `max_parallel` flag gates concurrency; and
`tasks.md`'s `depends:` edges are the implicit task graph — no per-task nodes are
built or persisted.**

Selected design direction: **Approach 3 — one developer spawn per task +
max_parallel + pure-orchestration driver** (complexity **M**). Recorded in
`design.md`. The two sub-DAG approaches (persisted / ephemeral per-task node
graph) are now the rejected alternatives — they build four new step contracts, a
parser, and a `subdag.py` for a graph that `tasks.md` `depends:` already
expresses.

- **Per-task dispatch:** `execute-next-task` keeps `repeat_until:
  all_tasks_completed`; only its contract instruction changes — from "complete
  all tasks in one spawn" to "implement exactly one assigned ready task —
  implement, verify, commit, mark `[x]` — then return." `repeat_until` +
  `readiness.repeat_until_redispatch` already re-fire a step while its predicate
  is false; the per-task loop primitive already exists.

- **Task graph:** `tasks.md` `depends:` edges are the implicit graph; a task is
  ready when it is `- [ ]` and every id in its `depends:` is `- [x]`. **No
  per-task nodes are written to `state.yaml`.** `workflow_plan[implement].nodes`
  keeps its single `execute-next-task` node. `tasks.md [x]` markers are the
  durable per-task state — the same role they play today.

- **The agent owns its commit and `[x]` marking.** This is intentional and
  correct — the commit is intrinsic to the unit of work and the agent holds the
  judgment (what to commit, what message). There are **no** `commit-task` /
  `mark-task-done` / `regression-check` / `implement-task` contracts, no
  `subdag.py`, no `expand:` field.

- **`max_parallel` flag** — behavioral, default `1` (sequential). The first
  integer `behavioral:` flag; `--max-parallel` CLI binding. At `1` the driver
  dispatches one task at a time; at `> 1` it spawns up to N developer agents
  concurrently for independent ready tasks. `orchestrator ready` (already
  shipped, ORC-63) is the ready-set primitive.

- **Driver = pure orchestration.** The orchestrate-skill dispatch loop runs
  `orchestrator next`/`ready` → spawn → `orchestrator done` → repeat and carries
  no deterministic ticket or state side effects. The anti-pattern ORC-66 removes
  is the *driver/skills* folding ticket bookkeeping into orchestration — not the
  agent committing its own code.

Resolutions for the open questions:

- **OQ-3 — regression seam:** with one spawn = one task, the regression
  count/compare and the retry decision both stay agent-side, scoped to the
  agent's one task. There is no separate `run:` node and no driver involvement.

- **OQ-4 — stash/rollback:** out of scope for ORC-66; remains agent-side,
  coupled to the retry loop. Filed as a follow-on ticket.

- **OQ-1 / OQ-2 (from earlier revisions) are void** — they presupposed the
  per-task `run:`-node model the user has now rejected.

- **OQ-1 (the original "wait for ORC-65" question) is void** — ORC-65 is
  obsolete; ORC-66 builds the per-task DAG itself.

## Open Questions

- OQ-1: **Must ORC-66's run: node wiring (AC-2/3/4) wait for ORC-65?** ORC-65 is at
  explore-completed with design-and-draft-artifacts pending. Per-task `commit[run]` and
  `mark-task-done[run]` nodes need a per-task node in the DAG to wire `depends_on` to.
  If ORC-65 lands first, ORC-66 slots in cleanly. If ORC-66 ships before ORC-65, the
  commit and mark side-effect steps could be added to the monolithic execute-next-task
  loop as a transitional form — but that partially re-bundles what the ticket is trying
  to separate. The architect should decide: (a) wait for ORC-65, (b) ship a partial
  ORC-66 scoped to documentation + classification only, or (c) land the run: nodes
  inside the current loop as an intermediate step.

- OQ-2: **tasks.md `[x]` marking — agent or run: node?** Today the developer agent
  self-marks its task as complete (execute-next-task.yaml line 88: `Mark task complete
  in tasks.md: [ ] → [x]`). Under the ORC-65 model where each task becomes a DAG node,
  does the agent continue self-marking, or does a downstream `mark-task-done[run]` node
  handle it? The latter is more consistent with the ticket's intent but requires the
  agent to emit the task ID as a declared output so the script knows which checkbox to
  flip.

- OQ-3: **Regression comparison — where is the judgment/determinism split?**
  execute-next-task.yaml lines 61-85 embed both counting (deterministic: parse stdout,
  count passing tests) and comparison (deterministic: `current < baseline`). Both can
  move to a `regression-check[run]` node. But the decision "this regression is
  acceptable" requires judgment — if the script detects a regression, does it block and
  hand back to the agent, or is the agent always re-dispatched? The architect should
  confirm the seam.

- OQ-4: **Stash/rollback as a run: node or keep in agent?** Rollback on failure
  (execute-next-task.yaml:98-126, `git stash push/pop`) is a deterministic operation
  but it is tightly coupled to the agent's retry loop. Extracting it requires a
  structured failure signal from the agent to the dispatcher so the run: node knows
  whether to stash or drop. Determine whether this extraction is in scope for ORC-66
  or is a follow-on.

## Technical Context

### Key files and evidence

| File | Relevance |
|------|-----------|
| `config/steps/execute-next-task.yaml:88-95` | Agent self-marks `[x]`, stages files, runs git commit — deterministic side effects bundled in agent loop |
| `config/steps/execute-next-task.yaml:98-126` | git stash push/pop/drop inside agent on failure — deterministic but tightly coupled to retry loop |
| `config/steps/execute-next-task.yaml:61-85` | Regression count compare embedded in agent (deterministic) — candidate for `regression-check[run]` |
| `config/steps/bootstrap-commit.yaml` | Canonical 3-line `run:` step shape — the pattern to follow |
| `config/scripts/inline/` | 19 existing inline scripts — run: lane is well-established |
| `config/steps/contracts/migration-run-field.md` | Documents how to add `run:` to a step contract; already exists |
| `config/steps/contracts/auto-commit.md` | Documents commit message format; an extracted `commit-task[run]` would be its primary consumer |
| `config/scripts/orchestrator_next/dispatch.py:326-358` | ORC-45 two-path dispatch; no changes needed |
| `skills/developer/SKILL.md:22,64-68` | Developer skill already delegates ticket transitions to `/backlog-manager` — good prior art for AC-3 |
| `skills/reviewer/SKILL.md:48-58` | Reviewer skill delegates ticket transitions to `/backlog-manager` — satisfies AC-3 today |
| `skills/linear/SKILL.md` | Linear operations via MCP; no direct `backlog task edit` or git inside agent spawn — already clean |
| `spec/project.yaml:223-237` | Existing `rules:` block — AC-5 adds one named rule here |
| `config/steps/CONVENTIONS.md:49-55` | Single Responsibility Principle section — AC-6 adds classification section adjacent to this |
| `config/steps/contracts/rule-merge.md` | Rule propagation algorithm; project.yaml rule (AC-5) reaches every step via source-5 in the merge |
| `feature_worktrees/orc-65/spec/changes/orc-65/state.yaml` | ORC-65 status: explore completed, design-and-draft-artifacts pending — not yet in design |

### Current step classification snapshot (high-level)

**Already `run:` (correctly classified, no changes needed):**
`archive-completed-change`, `bootstrap-commit`, `capture-test-baseline`, `check-bootstrap-state`,
`compute-prediction-accuracy`, `compute-swe-metrics`, `detect-language`, `git-init`,
`mark-change-completed`, `merge-to-main`, `preview-route`, `register-with-orchestrator-home`,
`remove-worktree`, `run-quality-baseline`, `setup-claude-md`, `setup-claude-settings`,
`setup-portless`, `verify-report`, `write-bootstrap-state` — 19 steps.

**`agent:` steps — correctly classified (judgment required):**
`design-and-draft-artifacts` (architect), `diagnose` (discoverer), `explore` (discoverer),
`generate-project-yaml` (developer), `install-tooling` (developer), `run-learn-cycle`
(workflow-learner), `run-phase-review` (reviewer), `run-ux-critique` (ux-reviewer),
`ux-design` (ideator).

**`agent:` step with embedded deterministic side effects — primary ORC-66 target:**
`execute-next-task` (developer) — bundles code authoring (judgment) with git commit,
tasks.md marking, stash/rollback, and regression comparison (all deterministic).

### ORC-45 two-path dispatch mechanics (no change required)
- `contract.agent` → dispatcher emits JSON, driver spawns agent, agent calls `orchestrator done`
- `contract.run` → dispatcher executes script synchronously, records result, exits 0 (no JSON)
- Both paths support `inputs:` / `outputs:` / `depends_on` (ORC-63)
- New run: steps need: YAML contract, `scripts/inline/<name>.sh`, bump `version:`, declare `inputs:` / `outputs:`
