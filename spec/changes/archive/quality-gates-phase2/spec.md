---
feature-id: quality-gates-phase2
linear-ticket: none
---

# Chore: Phase 2 Quality Gate Improvements

## What

Four quality gate enhancements to enforce staff-level quality at each workflow stage:

1. **Per-dimension review scores** — Expand `review_score` in state.yaml from a single integer to a structured object with per-dimension breakdown (spec_compliance, correctness, security, simplicity, code_quality)
2. **Artifact validation gate** — New `validate-artifacts.yaml` step contract for structural compliance checking before review
3. **Tighter explore verify** — Add verify assertions for minimum use case count and explicit build-or-reuse decision
4. **Quality baseline comparison** — Add historical baseline comparison in `run-phase-review` using `feature-metrics.jsonl`

Files modified:
- `config/steps/CONVENTIONS.md` — Update State Field Registry for expanded review_score
- `config/steps/run-phase-review.yaml` — Per-dimension scoring + baseline comparison
- `config/steps/explore.yaml` — Tighter verify assertions
- `config/workflows/feature.yaml` — Add validate-artifacts step to specify phase
- `config/workflows/bugfix.yaml` — Add validate-artifacts step to diagnose phase

Files created:
- `config/steps/validate-artifacts.yaml` — Structural artifact validation step

## Why

The current quality enforcement has gaps:
- Single-dimension scoring hides which aspects are weak (security? simplicity?)
- No structural validation before review wastes full review cycles on format issues
- Weak discovery briefs flow unchecked to the architect
- No regression detection against historical baselines

## Acceptance Criteria

- AC-1: CONVENTIONS.md State Field Registry shows `review_score` as object with `overall` and `dimensions` sub-fields
- AC-2: run-phase-review.yaml records per-dimension scores and compares against feature-metrics.jsonl baseline
- AC-3: validate-artifacts.yaml exists with structural compliance checking rules
- AC-4: feature.yaml specify phase includes validate-artifacts before run-phase-review
- AC-5: bugfix.yaml diagnose phase includes validate-artifacts before run-phase-review
- AC-6: explore.yaml verify includes minimum 2 use cases and explicit build-or-reuse decision assertions
