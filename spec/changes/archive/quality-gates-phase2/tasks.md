# Tasks — Phase 2 Quality Gate Improvements

- [x] T-1: Update review_score in CONVENTIONS.md State Field Registry to structured object
  Verify: grep CONVENTIONS.md for review_score row — shows type as `object` with example containing `overall` and `dimensions` keys

- [x] T-2: Add per-dimension scoring and baseline comparison to run-phase-review.yaml
  Verify: run-phase-review.yaml instruction includes writing per-dimension scores and reading feature-metrics.jsonl for baseline comparison
  depends: T-1

- [x] T-3: Create validate-artifacts.yaml step contract
  Verify: config/steps/validate-artifacts.yaml exists with structural compliance checking against artifact-formats.md

- [x] T-4: Add validate-artifacts to feature.yaml specify phase
  Verify: feature.yaml specify phase steps list includes validate-artifacts before run-phase-review
  depends: T-3

- [x] T-5: Add validate-artifacts to bugfix.yaml diagnose phase
  Verify: bugfix.yaml diagnose phase steps list includes validate-artifacts before run-phase-review
  depends: T-3

- [x] T-6: Tighten explore.yaml verify assertions
  Verify: explore.yaml verify section includes assertions for minimum 2 use cases and explicit build-or-reuse decision
