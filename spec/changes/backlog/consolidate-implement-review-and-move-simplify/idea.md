# Consolidate implement-phase review + move simplify into developer

## Problem

The implement phase currently runs three sequential reviewer-agent steps that overlap heavily in scope and re-read the same files:

1. `run-simplify` — reviewer scans changed code for simplification opportunities
2. `run-phase-review` — reviewer scores 5 dimensions + AC walkthrough + fix-task generation
3. `run-feature-verification` — reviewer walks ACs with evidence (again)

Observed in HL-282 (autopilot-2026-04-17-001):
- `run-simplify` spawned a reviewer for a one-line "no changes" report (~18s, ~30K tokens)
- `run-phase-review` did AC verification as part of scoring
- `run-feature-verification` would have re-done AC verification — I short-circuited it because T-9 + phase review had covered it

Three agent spawns where one would do. Simplify also runs at the wrong time (as a reviewer step after task completion) — it belongs in the developer's hands, before any review.

## Proposal

**One consolidated reviewer step** for the implement phase:

- Merge `run-simplify` + `run-phase-review` + `run-feature-verification` into a single step (e.g., `run-implement-review`)
- The single step does: code review + simplification report + 5-dimension scoring + AC verification with evidence
- Files read once, findings correlated (e.g., a simplification opportunity can become a fix task directly)

**Simplify moves into the developer:**
- After the last task in the phase completes, `execute-next-task` (or a new `developer-simplify-pass` step) runs a developer-owned simplify pass over the worktree changes
- Same context as the developer who wrote the code → faster, more informed
- Reviewer then reviews the simplified code (closer to what ships)

## Scope

**In-scope:**
- Update `config/workflows/feature.yaml` implement phase step list
- Create consolidated step contract
- Update `execute-next-task.yaml` (or add a trailing developer step) to include a simplify pass
- Deprecate `run-simplify.yaml`, `run-feature-verification.yaml` (or keep stubs that call into the new step)
- Update CONVENTIONS.md if step boundaries change

**Out-of-scope:**
- Specify-phase review (stays as-is)
- Complete-phase flow (unchanged)
- Final-signoff (separate step, unchanged)

## Acceptance criteria

- AC-1: Implement phase runs exactly one reviewer-agent spawn (not three)
- AC-2: Simplify runs before review, not after
- AC-3: AC verification produces evidence in the same step as dimension scoring
- AC-4: Review reports still contain all current fields (score per dimension, findings, AC evidence, fix tasks)
- AC-5: Existing archived state.yaml format unchanged (backwards-compat for /learn and /telemetry consumers)

## Why this matters

- Saves ~30K tokens + one agent spawn per feature
- Less context-switching for the reviewer (one read pass, not three)
- Simplify in the developer's hand = better chance of shipping simpler code (reviewer can't refactor without risking scope creep; developer can)

## Priority

Medium — quality-of-life workflow improvement, not blocking. Consider after the `compute-swe-metrics` bug is fixed so we can measure the savings.
