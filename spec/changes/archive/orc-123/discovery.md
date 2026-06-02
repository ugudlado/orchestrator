---
feature-id: orc-123
linear-ticket: orc-123
---

# Discovery Brief: Add design workflow — produce reviewed artifacts ready to implement

## Feature Summary

Today the only workflow entry points that include a design phase (explore → design-and-draft-artifacts → design-review) are `feature` and `bugfix`, both of which continue into implementation. There is no way to stop cleanly after design-review passes. This feature adds a `design` workflow that terminates after design-review passes — leaving a reviewed `design.md` + `tasks.yaml` in the worktree — and a `/design` skill as its entry point. A follow-on `orchestrator patch <id>` or `orchestrator feature <id>` on the same id should detect the existing artifacts and skip re-doing design steps.

## Personas & Actors

- **Developer** — wants to front-load the design phase on a non-trivial ticket, get artifacts reviewed, then commit to implementation separately.
- **Orchestrator engine** (DAG walker + seed-state.sh) — drives the workflow steps, seeds fresh state per schema while inheriting worktree context from prior runs.
- **Reviewer agent** — runs `design-review`, scores the design artifacts, and either passes or triggers a retry loop back to `design-and-draft-artifacts`.

## Use Cases

### Happy Path

UC-1: Design-only run — Developer invokes `orchestrator design orc-123`; the engine runs check-rerun → create-worktree → explore → design-and-draft-artifacts → design-review → run-learn-cycle, exits cleanly, leaving `design.md` and `tasks.yaml` in the worktree.

UC-2: Design then implement — Developer follows up with `orchestrator patch orc-123`; the engine seeds fresh state inheriting `worktree_path`/`branch`, and each step's per-step rerun guard (checking for existing artifact files) causes explore and design-and-draft-artifacts to short-circuit, so execution resumes at `implement-tasks`.

UC-3: Design review retry — design-review scores below threshold; `on_failure: design-and-draft-artifacts` routes back; the architect revises artifacts; design-review runs again (up to max_retries: 3).

### Error & Edge Cases

UC-E1: Already archived — check-rerun finds an archive dir for `orc-123`; it marks all plan nodes completed and signals workflow complete without re-running.

UC-E2: Design artifacts already present on patch — explore's rerun guard finds `discovery.md` already written; returns COMPLETION with `already_completed: true`; design-and-draft-artifacts similarly short-circuits on existing `design.md` + `tasks.yaml`.

UC-E3: design-review max retries exceeded — after 3 failures the DAG walker marks the step abandoned and the workflow exits with a blocked signal for user intervention.

## Scope

### In Scope

- `config/workflows/design.yaml` with steps: check-rerun, create-worktree, explore, design-and-draft-artifacts, design-review (on_failure + max_retries), run-learn-cycle.
- `/design` skill: thin dispatcher to `orchestrate $ARGUMENTS --schema design`.
- Cross-schema resume: `orchestrator patch <id>` after a completed design run should skip design steps via per-step rerun guards (existing artifact files act as skip signals).
- Verification that `orchestrator design <id>` CLI resolves correctly (dynamic subcommand from `config/workflows/design.yaml` presence).

### Out of Scope

- New step implementations — all required steps (check-rerun, create-worktree, explore, design-and-draft-artifacts, design-review, run-learn-cycle) already exist.
- DAG-level cross-schema state merging — resume is handled by per-step guards, not by copying `step_history` across state files (the engine does not support this and it is not needed).
- Modifying `patch.yaml` or `feature.yaml` — those schemas are unchanged; the resume story is symmetric and emergent.
- End-to-end integration testing infrastructure — manual canary verification per CLAUDE.md conventions.

## UI Direction

N/A — no UI components. CLI-only workflow addition.

## Key Decisions

- **Selected design direction (design-and-draft-artifacts)**: Approach 1 — new
  `config/workflows/design.yaml` schema reusing existing steps + a
  `skills/design/SKILL.md` dispatcher. Chosen over Approach 2 (a
  `--stop-after design-review` dispatcher flag) because subcommand resolution is
  already dynamic (no engine change) and each schema must be self-contained
  (ORC-108). Complexity: **S**. Both deliverables are already committed on this
  branch (39a57f8, a26714b), so the implementation tasks are verification-only
  (status: completed) per the check-rerun-does-not-inspect-HEAD pattern.
- **Per-step rerun guards vs DAG-level state merging**: `seed_write_state.py` always starts `step_history: []` for a new schema run. Cross-schema skip relies on each step's own artifact-presence guard (e.g. `explore` checks for `discovery.md`, `design-and-draft-artifacts` checks for `design.md`/`tasks.yaml`). This is already the existing pattern — no engine changes needed.
- **Build vs reuse**: All required workflow steps already exist. This feature is purely additive: one new YAML file + one skill file. Both already committed in 39a57f8 and a26714b.
- **CLI subcommand resolution**: `orchestrator design <id>` resolves dynamically because `_workflow_subcommands()` discovers any `config/workflows/*.yaml` file. No CLI code change needed.

## Open Questions

- OQ-1: Does `run-learn-cycle` in the design schema capture design-phase learnings correctly, or does it assume implementation artifacts (e.g. `tasks.yaml` diff) that may not exist yet? The prompt.md should be reviewed to confirm it handles the design-only context gracefully.
- OQ-2: AC#3 says "resumes from implement-tasks without re-running design steps" — but `patch.yaml` doesn't include `explore` or `design-and-draft-artifacts` as steps. Does the ticket mean `orchestrator feature <id>` (not `patch`) for the resume scenario, since `patch` skips design entirely by schema?
- OQ-3: The explore step's rerun guard checks the archive dir for a completed state, not for existing `discovery.md` in the worktree. After a design run (not archived, just exited cleanly), the guard won't trigger on a subsequent feature/patch run — the full explore will re-run. Is this acceptable, or should the guard also check for existing artifact files?
