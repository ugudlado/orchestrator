# Tasks -- Merge final verification into implement review

- [x] T-1: Add AC-verification-with-evidence section to run-phase-review.yaml instruction
  Verify: run-phase-review.yaml instruction contains steps that (a) read spec.md or fix-plan.md acceptance criteria, (b) verify each AC with evidence, (c) handle ALL/EVERY/EACH quantifiers with exhaustive N/N counts, and (d) include the FIXED/RESOLVED fresh-search rule from run-feature-verification

- [x] T-2: Remove run-feature-verification from _complete-phase.yaml and clean up orphaned assertions/metrics
  Verify: _complete-phase.yaml steps list does not contain run-feature-verification; assertions do not reference "acceptance criteria verified with evidence"; review_score metric is removed from complete phase (no step produces it); run-feature-verification.yaml file still exists in config/steps/
  depends: T-1

- [x] T-3: Update artifact-formats.md consumer references and validate all workflow schemas
  Verify: In contracts/artifact-formats.md Specification Format Contract consumers section, run-feature-verification is replaced by run-phase-review for AC reading; feature.yaml, bugfix.yaml, and chore.yaml all include _complete-phase without referencing run-feature-verification in their override assertions
  depends: T-2
