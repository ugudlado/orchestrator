---
id: ORC-27
title: Document or script the initial state.yaml seed for /orchestrate
status: Done
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:53'
labels:
  - slug-document-or-script-state-seeding
  - bug
  - score-9.0
  - recurrence-2
dependencies: []
references:
  - >-
    Autopilot 2026-05-02 — claimed-done-not-on-disk failure for
    cost-summary-on-archive.
  - >-
    Autopilot 2026-05-03 — same gap re-encountered when picking
    fix-archive-backlog-cleanup-tests; run halted before dispatch.
priority: high
ordinal: 26000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: document-or-script-state-seeding -->

**Original score:** 9.0 | **Recurrence:** 2

## Idea

The orchestrate skill prose dispatches via `orchestrator next $WORKFLOW_STATE_DIR/<slug>/state.yaml`, which requires that file to already exist. Nothing in `skills/orchestrate/` ships a script that creates it. `select-workflow.yaml` is documented as "pre-init", `workflow-init` populates `workflow_plan` and other fields *into* state.yaml but does not bootstrap the file from nothing. The seed shape is implicit driver knowledge — no schema, no template, no `--init` subcommand on the CLI.

Result: every fresh `/orchestrate` invocation requires the driver (Claude) to write a state.yaml from memory before the workflow can run. That memory-only step is exactly the failure mode that bit autopilot 2026-05-03 (run claimed completed but no state.yaml on disk).

Two paths, pick one:

- **A**: Ship `skills/orchestrate/scripts/seed-state.sh <slug> <schema> <flags-json>` that writes the canonical shape (`schema`, `change_id`, `slug`, `created_at`, `phase: main`, `status: active`, `flags: {...}`, `step_history: []`) and runs `generate_plan` to produce plan.yaml. Document it in SKILL.md as Step 1 of dispatch.
- **B**: Extend `orchestrator next` to auto-seed state.yaml if it's absent, given the schema and resolved flags as CLI args (`orchestrator next <path> --init schema=bugfix --flag auto=true ...`). More CLI surface but moves the seed shape into validated code.

## Why Now

- Bit two autopilot sessions in a row (2026-05-02 and 2026-05-03). The first session "completed" iter 1 of cost-summary-on-archive in conversation memory but persisted nothing — discoverable only when the second session looked for the state file.
- Blocks deterministic resumability of `/autopilot`. The whole point of the simplification refactor (commit fa6112d) is autopilot becomes a thin wrapper, but the wrapper can't dispatch cleanly without manual state seeding.
- One file fix (option A) or one CLI subcommand (option B). Net cost: 50–100 lines.

## Scope

For option A:
1. New `skills/orchestrate/scripts/seed-state.sh` — bash, takes `--slug`, `--schema`, `--description`, `--flags-json`, writes `$WORKFLOW_STATE_DIR/<slug>/state.yaml`.
2. After writing, invoke `python -m orchestrator_next.generate_plan` to materialize plan.yaml.
3. Update `skills/orchestrate/SKILL.md` Section 2 ("Resume entry point") to add a "fresh init" path that runs the seeder when no state.yaml exists.
4. Test: a fresh slug + schema produces a valid state.yaml + plan.yaml that `orchestrator next` can immediately consume.

## Out of scope

- Changing the canonical state.yaml shape (already locked by `record.py` validation).
- Auto-detecting schema (that's `select-workflow`'s job; the seeder receives schema as input).
- Backfilling for in-flight runs (zero of those).

## Priority

- User value: 9/10 (every autopilot run depends on this; manual seeding is unsafe)
- Strategic fit: 9/10 (closes the deterministic-dispatch promise of the engine)
- Technical leverage: 9/10 (small fix, large blast radius — unblocks all autopilot work)
- Effort: small
- **Score: 9.0**

## Source

- Autopilot 2026-05-02 — claimed-done-not-on-disk failure for cost-summary-on-archive.
- Autopilot 2026-05-03 — same gap re-encountered when picking fix-archive-backlog-cleanup-tests; run halted before dispatch.

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New `skills/orchestrate/scripts/seed-state.sh` — bash, takes `--slug`, `--schema`, `--description`, `--flags-json`, writes `$WORKFLOW_STATE_DIR/<slug>/state.yaml`.
- [ ] #2 After writing, invoke `python -m orchestrator_next.generate_plan` to materialize plan.yaml.
- [ ] #3 Update `skills/orchestrate/SKILL.md` Section 2 ("Resume entry point") to add a "fresh init" path that runs the seeder when no state.yaml exists.
- [ ] #4 Test: a fresh slug + schema produces a valid state.yaml + plan.yaml that `orchestrator next` can immediately consume.
<!-- AC:END -->
