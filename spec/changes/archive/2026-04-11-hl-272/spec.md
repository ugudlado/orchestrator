---
feature-id: merge-final-verification-into-implement-review
linear-ticket: HL-272
---

# Chore: Merge final verification into implement review

## What

Merge the acceptance-criteria-verification-with-evidence behavior from `run-feature-verification` into `run-phase-review`, then remove `run-feature-verification` from the shared complete phase step list. Clean up complete phase assertions and metrics that become orphaned after the removal.

Files affected:
- `config/steps/run-phase-review.yaml` -- add AC verification section to instruction
- `config/workflows/_complete-phase.yaml` -- remove run-feature-verification from steps, clean up assertions/metrics
- `config/steps/contracts/artifact-formats.md` -- update consumer references from run-feature-verification to run-phase-review

Files explicitly NOT deleted:
- `config/steps/run-feature-verification.yaml` -- retained for potential standalone use

## Why

The orchestrator workflow has two overlapping reviewer-agent spawns that both run verify_commands and check acceptance criteria. `run-phase-review` (end of implement phase) scores five dimensions and checks schema verify assertions. `run-feature-verification` (start of complete phase) verifies every AC with evidence. Both use the reviewer agent and read the same spec and code.

Merging eliminates the redundant agent spawn, reduces wall-clock time, and consolidates all quality checks into a single implement-phase review pass. The complete phase becomes purely archival and metric-collection.

## Acceptance Criteria

- AC-1: `run-phase-review.yaml` instruction includes an AC-verification-with-evidence section that reads spec.md (or fix-plan.md for bugfix) acceptance criteria, verifies each with evidence, and handles ALL/EVERY/EACH quantifiers with exhaustive counts.
- AC-2: `_complete-phase.yaml` steps list no longer includes `run-feature-verification`.
- AC-3: `_complete-phase.yaml` assertions and metrics are updated to reflect that AC verification now happens in the implement phase (no orphaned assertions that no step can satisfy).
- AC-4: All three workflow schemas (feature.yaml, bugfix.yaml, chore.yaml) that include `_complete-phase` continue to have valid step references (no broken includes or dangling references).
- AC-5: `run-feature-verification.yaml` file is NOT deleted from `config/steps/`.
- AC-6: Consumer references in `contracts/artifact-formats.md` are updated to reflect that `run-phase-review` (not `run-feature-verification`) now reads acceptance criteria for verification.
