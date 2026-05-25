# Tasks — {title}

- [ ] T-1: Write tests for {component} (RED — tests must fail)
  Why: {which design.md AC / decision this task serves}
  Files: {test files this task creates or touches}
  Change: {what the new tests assert, and why they fail today}
  Test scenarios:
    - {behavior the tests should cover}
    - {behavior the tests should cover}

- [ ] T-2: Implement {component} (GREEN — make tests pass)
  Why: {which design.md AC / decision this task serves}
  Files: {exact source files this task touches}
  Change: {the mechanism in 1-2 sentences — what edit, at which file:line. Not the goal, the actual change.}
  Test scenarios:
    - all T-1 tests pass
    - type-check clean
  depends: T-1

- [ ] T-3: Review checkpoint — {group} (phase gate)
  Why: phase gate — confirm the group's changes integrate cleanly before moving on
  Test scenarios:
    - type-check clean
    - full test suite green
    - build passes
  depends: T-2

<!-- Format contract: config/steps/design-and-draft-artifacts/prompt.md § Tasks YAML Format Contract -->
<!-- Each task carries indented fields: -->
<!--   Why            — the design.md AC / decision the change serves (the reason). -->
<!--   Files          — exact files the task touches (non-gate tasks). -->
<!--   Change         — the mechanism: what edit, at which file:line. Not the goal. -->
<!--   Test scenarios — a bulleted list of behaviors the task's tests should -->
<!--                    cover; the developer may add more (required). -->
<!--   depends        — `depends: T-N` or `depends: T-N, T-M` (optional). -->
<!-- Do NOT embed literal code or diffs in Change — mechanism-level prose only. -->
<!-- The RED→GREEN pairing is carried by `depends:` — a GREEN task depends on -->
<!-- its RED task; no separate test-sequencing field is needed. -->
<!-- Status markers: [ ] pending, [x] done. -->

<!-- TDD: when tdd_required, a RED test task precedes each GREEN implementation task. -->
<!-- A pure mechanical change (rename/move/delete, no behavior change) has no -->
<!-- meaningful RED step — sequence a regression-guard test task instead, and -->
<!-- annotate the task `(no RED — mechanical change)`. Its Test scenarios are -->
<!-- the regression-guard assertions. Do not fabricate a failing test that -->
<!-- does not reflect the actual change. -->

<!-- Group related tasks under `## Group X — <name>` headings when a feature -->
<!-- has multiple independent workstreams. Close each group with a review -->
<!-- checkpoint (phase gate). -->

<!-- VERIFICATION BUGS: if verification reveals new issues, add them as tasks -->
<!-- before proceeding — do NOT skip ahead. -->
<!-- Example: -->
<!-- - [ ] T-2b: Fix {bug found during T-2 verification} -->
<!--   Why: found during verification — {description} -->
<!--   Files: {file} -->
<!--   Change: {the fix mechanism} -->
<!--   Test scenarios: -->
<!--     - original test + new regression test pass -->
<!--   depends: T-2 -->
