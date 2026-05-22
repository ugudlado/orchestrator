---
feature-id: ORC-66
linear-ticket: none
---

# Design: One developer spawn per task + max_parallel + pure-orchestration driver + step classification

## Context

The orchestrator dispatches steps in two lanes (ORC-45): `agent:` spawns an LLM,
`run:` executes a script. ORC-63 turned the dispatcher into a DAG walker —
`workflow_plan[phase]` is `{nodes:[{id,depends_on,status}], verify}` and
`readiness.py` is the single readiness/status engine. ORC-63 also added
`orchestrator ready`, which returns a JSON array of all currently-ready node ids.

One step is out of step with both the DAG model and the LLM-vs-script principle:
`execute-next-task`. It is a single `agent: developer` spawn with
`repeat_until: all_tasks_completed`; that one spawn loops over **every** task in
`tasks.md` — the contract states verbatim "One developer spawn completes all
tasks." The orchestrator sees one recurring step, not N tasks: no per-task
telemetry, no per-task spawn isolation, no path to parallelism, and a single
agent context carrying the whole implementation phase.

**Scope history (2026-05-22).** Earlier revisions of this design explored
forward-declaring per-task `run:` nodes for a separate ticket (ORC-65, now
obsolete), then building a per-task sub-DAG — first ephemeral, then persisted
into `state.yaml`. **The user has settled on a simpler model that supersedes all
of those**: one developer agent spawn per task; the agent owns the complete unit
of work (implement, verify, commit, mark its task `[x]`); the driver does pure
orchestration; a `max_parallel` flag gates concurrency; and `tasks.md`'s
`depends:` edges are the implicit task graph — **no per-task nodes are built or
persisted**. This design adopts that model in full; the sub-DAG approaches are
recorded below only as rejected alternatives.

ORC-63 is `Done` and merged. Verified facts grounding this design:

- `orchestrator ready` exists (`bin/orchestrator` `_ready_verb` →
  `readiness.ready_nodes`) and returns a JSON array of ready node ids — the
  parallel primitive is already shipped.
- `repeat_until: all_tasks_completed` already works:
  `readiness.repeat_until_redispatch` re-fires a completed step whose
  `repeat_until` predicate is still false. The predicate `all_tasks_completed`
  is "zero `- [ ]` lines in `tasks.md`."
- `parse_tasks` (`record.py:803`) counts `[x]`/`[ ]`/`[~]` markers — the
  readiness signal, reused unchanged.
- `flags.yaml` has a `behavioral:` block (today all booleans) and a `cli:` block
  binding `--flag` strings to flag values.
- The orchestrate-skill dispatch loop (`skills/orchestrate/SKILL.md`) is a
  `LOOP` that calls `orchestrator next` → spawns one agent → `orchestrator
  done` → repeats; it already spawns agents with `run_in_background: true`.

## Goals / Non-Goals

### Goals

- **One developer spawn per task.** `execute-next-task` dispatches one developer
  agent for one task, not one spawn for all tasks. The single repeat_until
  all-tasks loop is replaced by per-task dispatch.
- **The agent owns its task end to end.** Within its one spawn the developer
  agent implements, runs verification, **commits its own changes, and marks its
  task `[x]` in `tasks.md`**, then returns COMPLETION. The commit is part of
  doing the task — the agent owning its unit of work, not deterministic glue.
- **`depends:`-ordered dispatch.** A task is dispatched only when every task in
  its `depends:` field is `[x]`. `tasks.md` `depends:` edges are the implicit
  task graph; nothing is persisted as per-task nodes.
- **`max_parallel` flag.** A behavioral flag, default `1` (sequential). At `1`
  the driver dispatches one task at a time. At `> 1` the driver may spawn up to
  N developer agents concurrently for independent ready tasks.
- **Pure-orchestration driver.** The orchestrate-skill dispatch loop runs
  `orchestrator next`/`ready` → spawn → `orchestrator done` → repeat and
  performs no deterministic ticket or state side effects of its own.
- **Durable classification discipline.** A named rule in `project.yaml rules:`
  and a `§ Step Classification` section in `CONVENTIONS.md` carry the
  LLM-vs-script litmus test forward via rule-merge and as authoring guidance.
- **Step classification audit.** Every step contract classified `agent:` /
  `run:` against the litmus test.

### Non-Goals

- **A persisted per-task node graph.** No per-task nodes are written into
  `state.yaml`; `workflow_plan[implement].nodes` keeps its single
  `execute-next-task` node. The task graph stays implicit in `tasks.md`.
- **New step contracts or inline scripts for per-task glue.** No `commit-task`,
  `mark-task-done`, `regression-check`, `implement-task`, no `subdag.py`, no
  `parse_tasks_dag` node builder, no `expand:` contract field — all dropped
  versus earlier revisions. The agent commits and marks its own task.
- **A new CLI subcommand.** `orchestrator ready` already exists; the driver
  consumes it.
- **`git stash` rollback extraction (OQ-4).** Coupled to the agent retry loop;
  follow-on ticket. (Resolves OQ-4.)
- **Changing `generate_plan.py` or the `workflow_plan` shape.** Top-level
  planning is untouched.
- **Parallel dispatch across workflow steps.** `max_parallel` governs only
  per-task developer spawns within `execute-next-task`; phase/step ordering is
  unchanged.

## Approaches Considered

### Approach 1: persisted per-task node graph — expand tasks.md into 4N nodes

`dispatch.py` expands `tasks.md` into a per-task node graph (`implement` +
`commit`/`mark`/`regression` `run:` nodes) and writes 4N nodes into
`workflow_plan[implement]`; the DAG walker schedules them.

- **Pros:** Full per-node telemetry; the task graph is inspectable in
  `state.yaml`.
- **Cons:** Heavy — a new `subdag.py`, a `parse_tasks_dag` node builder, an
  `expand:` contract field, four new step contracts + three inline scripts, a
  one-time-expansion guard, a `generate_plan` status-reset hazard. It also splits
  the agent's own commit out into a separate `run:` node — but committing your
  own work is the agent owning its unit, not glue. Over-built for the need.
- **Complexity:** L

### Approach 2: ephemeral per-task sub-DAG — regenerate every call

Same per-task chain as Approach 1 but regenerated in memory each
`orchestrator next` call, with node status derived from `step_history` +
`tasks.md`.

- **Pros:** Nothing persisted; no `generate_plan` rebuild hazard.
- **Cons:** Status-derivation logic for every call; still introduces the four
  contracts, the parser, and the `subdag.py` machinery. Same over-build as
  Approach 1 minus the persistence.
- **Complexity:** XL

### Approach 3: one developer spawn per task + max_parallel + pure-orchestration driver

`execute-next-task` keeps `repeat_until: all_tasks_completed`, but its contract
changes from "complete all tasks in one spawn" to "implement exactly **one**
ready task — implement, verify, commit, mark `[x]` — then return." `tasks.md`'s
`[x]` markers and `depends:` edges are the task graph and the durable state. The
driver reads the ready-task set (`orchestrator ready` + a `tasks.md` ready scan)
and spawns one developer agent per ready task, bounded by the `max_parallel`
flag (default 1). No per-task nodes, no new contracts, no `subdag.py`. The agent
commits and marks its own task; the driver does pure orchestration.

- **Pros:** The simplest model that delivers per-task spawns and a parallelism
  path. Reuses three existing primitives unchanged — `repeat_until`,
  `orchestrator ready`, `parse_tasks`. No new step contracts, no parser, no
  `state.yaml` shape change. Per-task spawn isolation gives per-task telemetry
  (one `step_history` entry per task spawn) without a persisted node graph. The
  agent owning its commit matches the reframed litmus principle.
- **Cons:** Per-task telemetry granularity is at the spawn level, not a 4-node
  chain — acceptable, the spawn *is* the unit. `repeat_until` re-dispatch fires
  the same step id N times; `step_history` carries N `execute-next-task` entries
  (already true today for retries; the metrics layer handles repeated ids).
- **Complexity:** M

### Selected Approach

**Approach 3.** The user's decision is explicit: *"orchestrator next will just
check if current step is execute-next-task and see if tasks.md have uncompleted
tasks, then return uncompleted ones till all tasks are completed"*; *"spin 1
developer agent for each task and can parallelize if needed or max parallelism
is set"*; *"let agent commit changes, verify and then return to driver"*; *"this
means driver is purely running orchestration."* Approaches 1 and 2 are rejected:
both build a per-task node graph (persisted or ephemeral) with four new
contracts, a parser, and a `subdag.py` — machinery the chosen model does not
need, since `tasks.md` `depends:` edges already *are* the graph and the agent
already does its own commit. Approach 3 reuses `repeat_until`, `orchestrator
ready`, and `parse_tasks` and adds only: a per-task-scoped `execute-next-task`
contract, a `max_parallel` flag, and a driver dispatch-loop change.

Auto-selection heuristic (XS=1..XL=5): Approach 1 = L(4), Approach 2 = XL(5),
Approach 3 = M(3). Approach 3 is both the user-directed model and the
lowest-complexity valid approach — heuristic and direction agree.

## The reframed organizing principle

ORC-66's intent is unchanged: **LLM where judgment is required, script where the
work is determined by state.** The reframing the user clarified is *where the
split falls*:

- The **driver** (the orchestrate-skill dispatch loop) does **pure
  orchestration** — call `orchestrator next`/`ready`, spawn, call `orchestrator
  done`, repeat. It carries **no** deterministic ticket or state side effects.
  Bookkeeping the driver or the skills fold into the loop (ticket transitions,
  state edits) is the anti-pattern ORC-66 removes.
- The **developer agent** does a **complete unit of work** — implement, verify,
  commit, mark its task `[x]`. The agent committing its own code is **not** a
  litmus violation: the commit is intrinsic to "doing the task," and the agent
  is the actor that knows the work is done and what the commit message should
  say (judgment). Splitting that commit into a separate `run:` node would be
  cargo-culting the litmus test, not applying it.

So the litmus test still governs every *step contract* (the classification
audit, AC-classification). What changes is the recognition that "the agent
commits its own task" is the agent owning its unit — and that the real
deterministic-glue anti-pattern lives in the *driver/skills* layer, which the
pure-orchestration audit (AC-driver) addresses.

## High-Level Design

### Architecture Overview

```
  orchestrate skill — dispatch LOOP
        │
        ▼
  orchestrator next  →  current step is execute-next-task,
        │                repeat_until: all_tasks_completed
        │
        │  tasks.md still has `- [ ]` items?
        │      NO  → repeat_until satisfied → step completes → next step
        │      YES ↓
        ▼
  driver reads the READY-TASK SET:
     orchestrator ready  +  a tasks.md scan for `- [ ]` tasks whose
     `depends:` are all `[x]`   →   [T-3, T-5, ...]   (independent ready tasks)
        │
        │  max_parallel flag (default 1)
        ├── max_parallel == 1 → spawn ONE developer agent for ready[0]
        └── max_parallel  > 1 → spawn up to N developer agents concurrently,
        │                       one per independent ready task
        ▼
  developer agent (per task)  — agents/developer.md
     implement the ONE task  →  run verification  →  git commit  →
     mark the task `- [x]` in tasks.md  →  return COMPLETION
        │
        ▼
  driver → orchestrator done (one call per finished spawn)
        │
        ▼
  LOOP back to `orchestrator next` — repeat_until re-dispatches
  execute-next-task until tasks.md has zero `- [ ]`
```

`workflow_plan[implement].nodes` is unchanged — one node, `execute-next-task`.
The per-task structure lives entirely in `tasks.md`. `tasks.md [x]` markers are
the durable state, exactly as today.

### Key Abstractions

- **Per-task spawn.** `execute-next-task`, when dispatched, implements exactly
  one task. The contract instruction changes from "complete all tasks" to
  "implement the one task assigned by the driver." `repeat_until:
  all_tasks_completed` re-dispatches the step until `tasks.md` has no `- [ ]`.

- **The ready-task set.** A task is *ready* when it is `- [ ]` and every id in
  its `depends:` field is `- [x]`. The driver derives this set each loop
  iteration from `orchestrator ready` plus a `tasks.md` scan. It is the set the
  driver spawns from.

- **`max_parallel`.** A behavioral flag (default `1`). It bounds how many
  developer agents the driver spawns concurrently for independent ready tasks.
  At `1` the loop is sequential — today's behavior, at per-task granularity.

- **Pure-orchestration driver.** The dispatch loop: `next`/`ready` → spawn →
  `done` → repeat. No deterministic side effects in the driver.

- **The litmus test.** *If a script given this exact input could produce the
  right output every time → `run:`. If it must weigh, interpret, or generate →
  `agent:`. Burden of proof on `agent:`.* The classification criterion for every
  step contract (see § Step Classification Audit).

## Low-Level Design

### Components

**Component A — `execute-next-task.yaml` rewrite (per-task contract).**
Responsibility: change the contract so one spawn does **one** task. Keep
`agent: developer`, `repeat_until: all_tasks_completed`, `inputs: [tasks.md]`,
`outputs: [task_execution_result]`. Rewrite the `instruction:` from the
all-tasks loop to: (1) the driver supplies the assigned `task_id`; (2) implement
that one task; (3) run project verification per `quality_bar`; (4) `git commit`
per `contracts/auto-commit.md`; (5) mark that task `- [x]` in `tasks.md`; (6)
return COMPLETION. Remove the "One developer spawn completes all tasks" line and
the all-tasks loop steps. Bump `version:`. The agent still owns the regression
gate, retry, and rollback for its one task — those stay agent-side (OQ-3/OQ-4).

**Component B — `max_parallel` flag in `flags.yaml`.**
Responsibility: register `max_parallel` under `behavioral:` with `default: 1`
and a description. It is the **first integer-valued behavioral flag** — the
block today holds only booleans; the flag value is an int, and the flag-merge
path must carry an int (verify no boolean coercion). Add a `cli:` binding
`--max-parallel: { sets: { max_parallel: <N> } }` so it can be set from the
command line. Declare it in the `execute-next-task` contract's `flags_read:`
block per `CONVENTIONS.md § Flag Dependencies` (it changes how the implement
phase is dispatched).

**Component C — driver dispatch-loop change (per-task spawn + bounded parallel).**
Responsibility: in `skills/orchestrate/SKILL.md`, when `orchestrator next`
returns the `execute-next-task` action, the driver: (1) reads the ready-task set
— call `orchestrator ready` and scan `tasks.md` for `- [ ]` tasks whose
`depends:` are all `[x]`; (2) reads `max_parallel` from `state.yaml.flags`
(default 1); (3) spawns up to `max_parallel` developer agents concurrently, one
per independent ready task, each with the assigned `task_id` in the spawn
prompt; (4) collects each COMPLETION and calls `orchestrator done` once per
finished spawn; (5) loops — `repeat_until` re-dispatches `execute-next-task`
until `tasks.md` has no `- [ ]`. At `max_parallel: 1` this is one spawn per
loop iteration. The harness already supports concurrent `Agent` calls and
`run_in_background: true`.

**Component D — developer agent contract (one task, owns commit + mark).**
Responsibility: `agents/developer.md` (and the developer skill) must describe
implementing **one** assigned task — implement, verify, commit, mark `- [x]`,
return — not draining a queue. Where the agent definition currently says
"complete all unchecked items," change it to "complete the one assigned task."
The agent owning its commit and `[x]` marking is intentional and correct (see §
reframed organizing principle).

**Component E — driver / skills pure-orchestration audit.**
Responsibility: audit `skills/orchestrate/SKILL.md`, `skills/developer/SKILL.md`,
`skills/reviewer/SKILL.md`, `skills/linear/SKILL.md` for any deterministic
ticket or state side effect performed by the *driver* or a *skill* inside or
around the dispatch loop. Verified prior art: the developer/reviewer skills
already delegate ticket transitions to `/backlog-manager` (a separate skill
invoked outside the dispatch loop) — `skills/developer/SKILL.md:24-26,64-66`,
`skills/reviewer/SKILL.md:24-26,53`; `skills/linear/SKILL.md` is thin MCP
wrappers (lines 35-54). The orchestrate skill's loop is already a thin
`next`/`done` wrapper (`SKILL.md:95-118`). The audit confirms the loop carries
no deterministic side effects; any found are removed or relocated per the litmus
classification. A regression-guard test locks the result.

**Component F — `project.yaml` rule (`step-classification`).**
A named rule, no `when:`, carrying the litmus test. Per
`contracts/rule-merge.md`, a project named rule with no `when:` is always active
and reaches every node's merged `rules:` via `generate_plan.py:223-240`.

**Component G — `CONVENTIONS.md § Step Classification`.**
A prose section with the litmus test as the step-authoring decision procedure,
placed between `§ Single Responsibility Principle` (lines 47-54) and `§
Structure` (line 56).

### Data Flow

Per-task dispatch (max_parallel = 1, the default):

```
orchestrator next → execute-next-task action  (repeat_until not yet satisfied)
        │
driver: ready set = orchestrator ready ∪ {tasks.md `- [ ]` with depends: all `[x]`}
        │  ready = [T-3]
        ▼
spawn developer agent, prompt carries task_id = T-3
        │
agent: implement T-3 → verify → git commit "feat(orc-NN): T-3 ..." →
       tasks.md: T-3 `- [ ]` → `- [x]`  → COMPLETION
        │
driver → orchestrator done   (one call)
        │
LOOP → orchestrator next → execute-next-task again (T-3 now [x],
       tasks.md still has [ ] items → repeat_until re-dispatches)
        ...
until tasks.md has zero `- [ ]` → repeat_until satisfied → step completes
```

Parallel dispatch (max_parallel = 3, independent ready tasks T-4, T-5, T-6):

```
driver: ready = [T-4, T-5, T-6]   (none depends on another)
        │  min(max_parallel, len(ready)) = 3
        ▼
spawn 3 developer agents concurrently, one per task (run_in_background: true)
        │
collect 3 COMPLETIONs → 3 × orchestrator done
        │
LOOP → repeat_until re-dispatches until tasks.md drained
```

AC-classification-rule propagation (the testable path):

```
project.yaml rules:  - id: step-classification (no when:)
        │  generate_plan.py — merge (rule-merge.md algorithm)
        ▼
named_rules{} → active_named[] → merged.extend(active_named)  # gen_plan:240
        ▼
state.yaml workflow_plan[phase].nodes[].rules[]  → every agent step sees it
```

### State Management

- **`workflow_plan[implement].nodes` — unchanged.** One node,
  `execute-next-task`, `status: pending` → `in_progress` → (after the last
  re-dispatch) `completed`. No per-task nodes are ever written.
- **`tasks.md [x]` markers — the durable per-task state.** The agent flips its
  task `- [ ]` → `- [x]` and stages `tasks.md` into its commit. This is the same
  durable checkpoint role `tasks.md` plays today. `repeat_until`'s
  `all_tasks_completed` predicate reads it.
- **`step_history`.** One entry per developer spawn — i.e. one per task (plus
  retries). `repeat_until` re-dispatch produces repeated `execute-next-task`
  entries, as retries already do today; the metrics layer aggregates by step id.
- **`flags.max_parallel`** — resolved at workflow init from
  `cli_flags > state_flags > schema_defaults` (= the `flags.yaml` default 1),
  stored in `state.yaml.flags`, read by the driver.
- **Resume.** `repeat_until` resume is unchanged: `orchestrator next`
  re-dispatches `execute-next-task` while `tasks.md` has `- [ ]` items. A
  workflow resumed mid-implementation reads `tasks.md`, finds the remaining
  `- [ ]` tasks, and continues. Crash mid-task: the task is still `- [ ]` (the
  agent marks `[x]` only after a successful commit), so it is simply re-dispatched.

### Error Handling

- **Crash mid-task.** The agent marks `- [x]` only after its commit succeeds; a
  crash before that leaves the task `- [ ]` — the next `orchestrator next`
  re-dispatches it. Idempotent: re-implementing an uncommitted task is safe.
- **A task's verification fails.** The agent retries within its spawn (existing
  Error Recovery Contract — retry cap, escalation); on exhausted retries it
  quarantines or escalates per the current protocol. Unchanged from today, now
  scoped to one task per spawn.
- **Regression detected.** The agent runs the regression gate for its one task
  (existing `execute-next-task` regression logic, retained agent-side); judgment
  on whether to retry stays with the agent's retry loop. (Resolves OQ-3 — the
  count/compare and the retry decision both stay agent-side now that one spawn =
  one task; there is no separate `run:` node.)
- **A parallel spawn fails while siblings succeed.** Each spawn's COMPLETION is
  recorded independently via its own `orchestrator done`. A failed task stays
  `- [ ]`; `repeat_until` re-dispatches it on a later iteration. Sibling
  successes are durable (committed + `[x]`).
- **`depends:` references an unknown task.** The driver's ready scan treats an
  unsatisfiable `depends:` as "not ready"; if no task is ever ready while
  `- [ ]` items remain, the loop surfaces a stall — the driver reports it rather
  than spinning. (A malformed `tasks.md` is a specify-phase artifact bug caught
  at phase review.)
- **`git stash` rollback (OQ-4).** Remains agent-side, coupled to the retry
  loop. Not extracted — follow-on ticket.

## Step Classification Audit

Every step contract in `config/steps/`, classified against the litmus test.
"Split candidate" = bundles judgment with deterministic side effects.

### Correctly `run:` (deterministic — 19 steps, no change)

`archive-completed-change`, `bootstrap-commit`, `capture-test-baseline`,
`check-bootstrap-state`, `compute-prediction-accuracy`, `compute-swe-metrics`,
`detect-language`, `git-init`, `mark-change-completed`, `merge-to-main`,
`preview-route`, `register-with-orchestrator-home`, `remove-worktree`,
`run-quality-baseline`, `setup-claude-md`, `setup-claude-settings`,
`setup-portless`, `verify-report`, `write-bootstrap-state`.

Litmus verdict: each consumes fixed inputs and produces the same output every
time. `run:` is correct.

### Correctly `agent:` (judgment required — 9 steps, no change)

`design-and-draft-artifacts` (architect), `diagnose` (discoverer), `explore`
(discoverer), `generate-project-yaml` (developer), `install-tooling`
(developer), `run-learn-cycle` (workflow-learner), `run-phase-review`
(reviewer), `run-ux-critique` (ux-reviewer), `ux-design` (ideator).

Litmus verdict: each must weigh, interpret, or generate. `agent:` is correct.

### Neither lane — pre-init contract (1 step, no change)

`select-workflow`. Declares neither `agent:` nor `run:` by design: a pre-init
step that runs before `state.yaml` exists and is not placed in any workflow's
`steps:` list. The litmus test classifies *dispatched* steps; a pre-init
contract has no lane. Listed for audit completeness — correct as-is.

### `agent:` step examined for bundling — `execute-next-task`

`execute-next-task` (developer) is correctly an `agent:` step: implementing a
task is judgment — read design.md, decide what to write, write it. Under this
design it implements **one** task per spawn and, as part of doing that task,
commits and marks `- [x]`. Per the reframed organizing principle, an agent
committing its **own** unit of work is **not** a judgment/side-effect bundle —
the commit is intrinsic to the task and the agent is the actor with the judgment
(what to commit, what message). So `execute-next-task` stays a single `agent:`
step; it is **not** split into per-task `run:` nodes. The deterministic-glue
anti-pattern ORC-66 targets is the *driver/skills* folding ticket bookkeeping
into orchestration — addressed by the pure-orchestration audit, not by splitting
the agent's commit out.

### AC-driver — driver and skills already keep ticket transitions out of the loop

Verified by grep against HEAD (worktree, 2026-05-22):

- `skills/orchestrate/SKILL.md` — the dispatch loop (lines 95-118) is a thin
  `orchestrator next` → spawn → `orchestrator done` wrapper; it performs no
  ticket edits or deterministic state mutations of its own.
- `skills/developer/SKILL.md` — claims work and moves the ticket to Code Review
  **via `/backlog-manager`** (lines 24-26, 64-66), a separate skill invoked
  outside the dispatch loop.
- `skills/reviewer/SKILL.md` — same: ticket transitions via `/backlog-manager`
  (lines 24-26, 53).
- `skills/linear/SKILL.md` — thin `mcp__plugin_linear_linear__*` wrappers
  (lines 35-54); no `backlog task edit`, no `git`, no commit.

**Verdict:** the driver/skills are already pure orchestration on the dispatch
path. The audit (Component E) confirms it and a regression-guard test locks it
in; any deterministic glue discovered is removed or relocated.

## Constraints

- `workflow_plan[implement].nodes` keeps its single `execute-next-task` node —
  no per-task nodes are written to `state.yaml`.
- `execute-next-task` keeps `repeat_until: all_tasks_completed`; the contract
  change is the instruction (one task per spawn), not the repeat mechanism.
- `tasks.md` `[x]` markers remain the durable per-task state and the
  `all_tasks_completed` signal.
- `max_parallel` is an integer flag (the first non-boolean `behavioral:` flag);
  the flag-merge path must carry an int without boolean coercion. Default `1`.
- At `max_parallel: 1` the dispatch behavior must equal today's sequential flow
  — one developer spawn at a time — differing only in per-task (not all-tasks)
  granularity.
- The driver/orchestrate-skill dispatch loop must carry no deterministic ticket
  or state side effects.
- The `project.yaml` named rule MUST use the named-rule format (`id:` + `rule:`)
  and be valid YAML; with no `when:` it is always active.
- The `CONVENTIONS.md` section MUST sit between `§ Single Responsibility
  Principle` and `§ Structure` with no dangling forward reference.
- Schemas and step contracts must not name a specific LLM tool
  (`project.yaml rules:` `agent-agnostic`). The litmus rule text is tool-agnostic
  by construction.

## Trade-offs

- **Per-task telemetry is at spawn granularity, not a 4-node chain** — one
  `step_history` entry per task spawn rather than four per task. Accepted: the
  spawn *is* the unit of work; a 4-node chain would need the
  persisted-node-graph machinery the user explicitly rejected.
- **`repeat_until` produces repeated `execute-next-task` step ids in
  `step_history`** — one per task (plus retries). Already true for retries
  today; the metrics layer aggregates by step id, so this is not new behavior.
- **No inspectable per-task DAG in `state.yaml`** — the task graph stays in
  `tasks.md`. Accepted: `tasks.md` is human-readable and `depends:`-explicit; a
  second representation in `state.yaml` would be duplication to keep in sync.
- **Parallelism is opt-in and bounded** — `max_parallel` defaults to 1, so the
  shipping behavior is sequential and conservative; concurrency is available
  when a run sets it. Shipping parallel-by-default would couple a behavior change
  to the structural change.
- **No dispatch-time bundling check** — a future bundled step is caught only by
  reviewer / workflow-improver judgment against `CONVENTIONS.md`. A static
  analyzer is disproportionate; the documented litmus test is the proportionate
  control.

## Acceptance Criteria

- AC-1: Given the step contracts in `config/steps/`, when the Step
  Classification Audit in design.md is read, then every contract is classified
  `agent:` or `run:` (or pre-init/no-lane) against the litmus test, and any step
  bundling judgment with deterministic side effects is identified and resolved.
  Verify: a test asserts the set of contract ids in design.md's audit equals the
  set of `config/steps/*.yaml` step-contract ids; the audit records each
  contract's lane. [traces: UC-E3]

- AC-2: Given `tasks.md` with multiple `depends:`-ordered tasks, when the
  workflow is at `execute-next-task`, then the CLI/driver dispatches **one
  developer agent per task** (a task is dispatched only when its `depends:` are
  all `[x]`), replacing the single `repeat_until` all-tasks loop, and each agent
  owns its task's implement, verify, commit, and `[x]` marking before returning.
  Verify: a test asserts the rewritten `execute-next-task.yaml` instruction
  scopes a spawn to one task (no "complete all tasks" language) and keeps
  `repeat_until: all_tasks_completed`; a dispatch test asserts one developer
  spawn is produced per ready task and the agent's COMPLETION reflects one task
  done with a commit and an `[x]` flip. [traces: UC-1, UC-2, UC-E1]

- AC-3: Given a `max_parallel` flag (default `1` = sequential), when the driver
  dispatches per-task developer spawns, then at `max_parallel: 1` it spawns one
  at a time and at `max_parallel: N` it spawns up to N concurrently for
  independent ready tasks, using `orchestrator ready` as the ready-task set
  primitive.
  Verify: a test asserts `max_parallel` is registered under `flags.yaml`
  `behavioral:` with `default: 1` and a `--max-parallel` `cli:` binding, and that
  the value flows into `state.yaml.flags` as an integer; a driver test asserts
  the spawn count per loop iteration is `min(max_parallel, len(ready_set))`.
  [traces: UC-2, UC-E2]

- AC-4: Given the orchestrate-skill dispatch loop and the developer/reviewer/
  linear skills, when audited, then the driver performs pure orchestration — the
  dispatch loop carries no deterministic ticket or state side effects — and any
  deterministic glue that lived in the driver or skills is removed or relocated
  per the litmus classification.
  Verify: a regression-guard test greps `skills/orchestrate/SKILL.md`'s dispatch
  loop and the developer/reviewer/linear skills and asserts no `backlog task
  edit --check-ac|--notes|--final-summary`, no `git commit`, and no direct
  state.yaml mutation appears in the driver's loop. [traces: UC-3]

- AC-5: Given `project.yaml` with a named rule `step-classification` carrying
  the litmus test, when `generate_plan` produces a plan for the feature schema,
  then the litmus-test rule text appears in the merged `rules:` of at least one
  agent step node.
  Verify: a test adds the `step-classification` rule to a fixture project.yaml,
  runs `generate_plan`, and asserts the rule text is present in
  `workflow_plan[phase].nodes[].rules` for an `agent:` node; a negative control
  without the rule asserts absence. [traces: UC-3]

- AC-6: Given `config/steps/CONVENTIONS.md`, when read, then a `§ Step
  Classification` section exists between `§ Single Responsibility Principle` and
  `§ Structure`, stating the litmus test as the step-authoring decision
  procedure, the burden-of-proof-on-`agent:` rule, and the unit-of-work split
  rule.
  Verify: a test finds a `## Step Classification` heading positioned after
  `## Single Responsibility Principle` and before `## Structure`; the section
  body contains the litmus-test sentence. [traces: UC-3, UC-E3]

- AC-7: Given a workflow resumed mid-implementation, when `orchestrator next` is
  called, then it re-dispatches `execute-next-task` while `tasks.md` has `- [ ]`
  items and the implement phase completes (no `repeat_until` re-dispatch) once
  every task is `- [x]` — `tasks.md [x]` markers are the durable resume signal.
  Verify: a test with a partially-completed `tasks.md` asserts `orchestrator
  next` returns `execute-next-task` until the last `- [ ]` is cleared, then the
  step completes. [traces: UC-1, UC-2]

## Decisions

- Per-task dispatch mechanism → **keep `repeat_until: all_tasks_completed` on
  `execute-next-task`; change the contract so one spawn implements one task**
  (user direction: "return the next ready task till all tasks are completed").
  `repeat_until` + `readiness.repeat_until_redispatch` already re-fire a step
  while its predicate is false — the per-task loop primitive already exists; only
  the contract's instruction changes.
- Task graph representation → **`tasks.md` `depends:` edges are the implicit
  graph; no per-task nodes are persisted** (user direction: no persisted node
  graph). The driver derives the ready set each iteration; `tasks.md [x]` is the
  durable state.
- The agent commits and marks its own task → **intentional and correct, not a
  litmus violation** — the commit is intrinsic to the unit of work and the agent
  holds the judgment (what to commit, what message). No `commit-task` /
  `mark-task-done` `run:` nodes.
- `max_parallel` → **a behavioral flag, default `1`** (user direction). The
  first integer `behavioral:` flag; `--max-parallel` `cli:` binding;
  `orchestrator ready` (already shipped) is the ready-set primitive.
- Driver = pure orchestration → **the dispatch loop carries no deterministic
  side effects; the driver/skills are audited and a regression test locks it**.
  The agent owning its commit is the agent's unit of work, not driver glue.
- Sub-DAG approaches (persisted / ephemeral per-task node graph) → **rejected**
  — they build four new contracts, a parser, and `subdag.py` for a graph that
  `tasks.md` `depends:` already expresses. The user chose the simpler model.
- OQ-3 (regression seam) → **the regression count/compare and the retry
  decision both stay agent-side**, scoped to the agent's one task — with one
  spawn = one task there is no separate `run:` node and no driver involvement.
- OQ-4 (stash/rollback) → **out of scope; follow-on ticket** — remains
  agent-side, coupled to the retry loop.
- OQ-1/OQ-2 (from earlier revisions) → **void** — they presupposed the per-task
  `run:`-node model the user has now rejected.

## Open Questions

- None. The per-task dispatch mechanism, the task-graph representation, the
  `max_parallel` flag, the pure-orchestration audit, resume, and OQ-3/OQ-4 are
  all resolved in Decisions above. The `git stash` rollback follow-on (OQ-4)
  should be filed as a separate backlog ticket but does not block ORC-66.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
