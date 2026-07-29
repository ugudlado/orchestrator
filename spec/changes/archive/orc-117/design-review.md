# Design Review: ORC-117 Engine Workflow-Agnosticism

## Scores

| Dimension      | Score | Notes |
|----------------|-------|-------|
| completeness   | 10    | All required sections, frontmatter, and AC traces present |
| ac_coverage    | 10    | Every AC maps to at least one task; every task `why` cites an AC |
| task_quality   | 9     | RED tests carry `@pytest.mark.xfail(strict=False)`; verify commands scoped. Minor: T-6 meta-test runs pytest via subprocess (double CI cost), but not a structural gap |
| feasibility    | 10    | Deletion-dominant diff; all touched symbols grep-verified in design; no new deps; backward-compat fallback |
| scope_control  | 10    | Non-Goals explicit; no task reaches outside stated Goals |
| **overall**    | **9** | Minimum of dimension scores |

## Verdict: PASS

Overall 9 ≥ project minimum 7; no critical findings.

## Notes

- **eval.sh path bug (pre-existing):** `eval.sh` references `skills/design-review/../../design/pack/validate-tasks-yaml.sh` but the script lives at `skills/design/validate-tasks-yaml.sh` (no `pack/` subdir). Running the validator directly confirms it passes. This is a broken path in eval.sh, not a design artifact defect.
- **T-6 meta-test:** `test_targeted_suite_green` runs the full affected pytest slice via subprocess. Functionally correct and explicitly scoped to exclude the 10 pre-existing failures. Slight CI overhead, not a blocker.
- Design is clean and ready for implementation.
