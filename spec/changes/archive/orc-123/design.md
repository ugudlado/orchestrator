---
feature-id: orc-123
linear-ticket: orc-123
---

# Design: Add design workflow — produce reviewed artifacts ready to implement

## Context

The orchestrator drives features through a DAG of steps defined by a workflow
schema YAML in `config/workflows/`. Two existing schemas — `feature` and
`bugfix` — run the design phase (`explore → design-and-draft-artifacts →
design-review`) and then continue straight into implementation. There is no
entry point that stops cleanly after `design-review` passes. To get reviewed
design artifacts without implementing, a developer must start a full workflow
and abandon it mid-flight, which leaves `state.yaml` in a half-run condition.

The CLI resolves subcommands dynamically: `_workflow_subcommands()`
(`bin/orchestrator:263`) globs `config/workflows/*.yaml`, so adding a schema
file adds a subcommand with no code change (`bin/orchestrator:286` maps
`orchestrator <name> <id>` → `run <id> --schema <name>`). Skills live as
dir-based entry points (`skills/<name>/SKILL.md`).

All steps this feature composes already exist (`check-rerun`,
`create-worktree`, `explore`, `design-and-draft-artifacts`, `design-review`,
`run-learn-cycle`). The work is therefore purely additive config: one schema
file and one skill dispatcher. Both are already committed on this branch
(39a57f8, a26714b); this design documents that shipped reality, and the tasks
verify it rather than re-implement it.

## Goals / Non-Goals

### Goals

- Provide a `design` workflow that terminates after `design-review` passes,
  leaving a reviewed `design.md` + `tasks.yaml` in the worktree.
- Provide a `/design` skill as a thin dispatcher to
  `orchestrate $ARGUMENTS --schema design`.
- Ensure `orchestrator design <id>` resolves end-to-end via the existing
  dynamic-subcommand mechanism.
- Capture any design-phase learnings via `run-learn-cycle` before exit.

### Non-Goals

- No new step implementations — every required step already exists.
- No changes to `patch.yaml` or `feature.yaml`.
- No engine changes for cross-schema `step_history` merging — resume relies on
  per-step artifact-presence guards, which already exist.
- No automated end-to-end test infrastructure — manual canary verification per
  CLAUDE.md conventions.

## Approaches Considered

### Approach 1: New `design.yaml` schema reusing existing steps

Add `config/workflows/design.yaml` listing the design-phase steps and ending at
`design-review` (loop back on failure) plus `run-learn-cycle`. Add a
`skills/design/SKILL.md` dispatcher. No code change — the CLI discovers the new
schema by glob.

- **Pros**: Zero engine changes; matches the established schema-per-YAML
  pattern (ORC-108); subcommand resolution is automatic; smallest possible diff.
- **Cons**: Cross-schema resume relies on each step's own rerun guard rather
  than a first-class "resume" concept (acceptable — it is the existing pattern).

### Approach 2: A `--stop-after design-review` flag on the feature schema

Add a CLI/dispatcher flag that runs the `feature` schema but halts the DAG walk
after a named step.

- **Pros**: No new schema file; one entry point.
- **Cons**: Introduces a new halt mechanism into the dispatcher (engine change,
  larger blast radius); makes the stop point implicit/configurable rather than a
  named, discoverable workflow; harder to surface as its own CLI subcommand and
  skill. Over-engineered for the need.

### Selected Approach

Approach 1. It is the boring, additive solution and matches the constraint that
each schema is self-contained and declares its own steps (ORC-108). It requires
no engine code change because subcommand resolution is already dynamic. Approach
2 was ruled out because it adds a halt mechanism to the dispatcher for a need
that a declarative schema already satisfies — complexity without justification.

## High-Level Design

### Architecture Overview

```
orchestrator design <id>
  └─ _workflow_subcommands() globs config/workflows/*.yaml  →  "design" present
       └─ run <id> --schema design  →  orchestrator-run.sh driver
            └─ DAG walk over design.yaml steps:
                 check-rerun → create-worktree → explore
                   → design-and-draft-artifacts → design-review
                       (on_failure → design-and-draft-artifacts, max_retries 3)
                   → run-learn-cycle  →  exit (worktree intact)
```

### Key Abstractions

- **Workflow schema YAML** — declarative step list; the unit of CLI subcommand
  discovery and DAG construction. No new abstraction introduced.
- **Per-step rerun guard** — each step's existing artifact-presence check
  (`explore` → `discovery.md`, `design-and-draft-artifacts` →
  `design.md`/`tasks.yaml`) is what makes a later `feature` run short-circuit
  the design steps. No new abstraction introduced.

## Low-Level Design

### Components

- `config/workflows/design.yaml` — the schema. Steps: `check-rerun`,
  `create-worktree`, `explore`, `design-and-draft-artifacts`, then
  `design-review` with `on_failure: design-and-draft-artifacts` and
  `max_retries: 3`, then `run-learn-cycle`. No implementation steps.
- `skills/design/SKILL.md` — `user-invocable` dispatcher whose Execution body is
  `orchestrate $ARGUMENTS --schema design`; auto-detects feature-id from
  worktree/branch when omitted.

### Data Flow

Discovery and design artifacts are written under
`spec/changes/<id>/` in the worktree (`discovery.md`, then `design.md` +
`tasks.yaml`). `state.yaml` for the design run lives alongside per the active
state-dir convention. On a later `feature` run, those files on disk are the skip
signals consumed by each step's rerun guard.

### State Management

`seed_write_state.py` always starts a fresh `step_history: []` for a new schema
run; it does not copy history across schemas. Cross-schema skip is therefore an
emergent property of artifact-presence guards reading files left on disk, not of
state merging. This feature adds no new state.

### Error Handling

- `design-review` below threshold → `on_failure` routes back to
  `design-and-draft-artifacts`; up to `max_retries: 3`, after which the DAG
  walker marks the step abandoned and the workflow exits blocked for user
  intervention (UC-E3).
- `check-rerun` finds an existing archive → marks plan nodes completed, signals
  complete without re-running (UC-E1).
- `run-learn-cycle` is best-effort: per its contract, any learning failure logs
  `learn_skipped` and returns success, so design-only context (no implementation
  artifacts) cannot fail the run (resolves OQ-1).

## Constraints

- Each schema must be self-contained and declare its own steps; no runtime tail
  injection (ORC-108 / b0165d6).
- Schemas must not reference any specific LLM tool (agent-agnostic).
- Subcommand resolution depends solely on the YAML file's presence in
  `config/workflows/`.

## Trade-offs

- **Resume via on-disk guards, not state merging.** Sacrifices a first-class
  "resume" concept for zero engine change. Acceptable because it is the existing,
  proven pattern and the discovery brief confirms no merging support is needed.
- **`run-learn-cycle` in a design-only run may have little to learn.** Accepted:
  it is best-effort and non-blocking, and keeps the design schema symmetric with
  feature/bugfix for the self-improving loop.

## Acceptance Criteria

- AC-1: Given `config/workflows/design.yaml`, when its steps are read, then they
  are exactly `check-rerun`, `create-worktree`, `explore`,
  `design-and-draft-artifacts`, `design-review` (with
  `on_failure: design-and-draft-artifacts`, `max_retries: 3`), `run-learn-cycle`,
  with no implementation step; `check-rerun` is the first step so an
  already-archived id signals complete without re-running.
  [traces: UC-1, UC-3, UC-E1, UC-E3]
- AC-2: Given the design schema file is present, when `_workflow_subcommands()`
  is evaluated, then `design` is in the returned set, so `orchestrator design
  <id>` dispatches to `run <id> --schema design`. [traces: UC-1]
- AC-3: Given `skills/design/SKILL.md`, when it is read, then it is
  `user-invocable: true` and its execution body routes to `orchestrate
  $ARGUMENTS --schema design`. [traces: UC-1]
- AC-4: Given a completed design run left `design.md` + `tasks.yaml` in the
  worktree, when a follow-on `feature`/`bugfix` run executes, then `explore` and
  `design-and-draft-artifacts` short-circuit via their artifact-presence guards
  and execution reaches `implement-tasks`. [traces: UC-2, UC-E2]
- AC-5: Given the design schema lists `run-learn-cycle` as its final step, when
  a design-only run reaches it, then it completes (or records `learn_skipped`)
  without failing the run, since learning is best-effort. [traces: UC-1]

## Decisions

- Additive config only (one YAML schema + one skill), both already committed on
  this branch (39a57f8, a26714b) → reuses existing steps and dynamic subcommand
  resolution → no engine code change; tasks are verification-only.
- Cross-schema resume via per-step artifact guards, not `step_history` merging →
  the engine does not support merging and it is not needed → resume is emergent
  and symmetric across schemas.
- **AC#3 wording — "patch" vs "feature" for resume.** The ticket's AC#3 says a
  subsequent `orchestrator patch <id>` resumes from `implement-tasks`. But the
  `patch` schema has no `explore`/`design-and-draft-artifacts` steps — it skips
  design by schema, so there is nothing to skip and its resume is structurally
  different from a `feature` resume. The meaningful "skip the already-done design
  steps" scenario is `orchestrator feature <id>`. AC-4 is therefore written
  against `feature`/`bugfix`. This is flagged, not silently resolved (see OQ-2).

## Open Questions

- OQ-2 (from discovery): AC#3 names `patch` for resume, but `patch.yaml` has no
  design steps so it cannot "skip re-running design steps." Confirm the intended
  resume command is `feature` (skip design via guards) — `patch` resume is a
  no-op with respect to design steps. AC-4 assumes `feature`/`bugfix`.
- OQ-3 (from discovery): the `explore` rerun guard's exact skip signal
  (archive-dir state vs. on-disk `discovery.md`) determines whether a non-archived
  design run is correctly skipped by a later `feature` run. If the guard checks
  only the archive dir, a clean-exit (non-archived) design run will re-run
  `explore`. Verify the guard reads on-disk artifacts; if not, that is a
  follow-up against the `explore` step, out of scope here.
