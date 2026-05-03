---
id: ORC-10
title: '`register-repo.test.sh` T-5b: update 2 assertions that test pre-FR-11 behavior'
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-register-repo-test-t5b-post-fr11-cleanup
  - feature
  - score-4.0
  - recurrence-1
dependencies: []
references:
  - >-
    single-source-metrics-via-step-events T-17 dev notes (declined to modify
    test outside allowed touch-set — correctly flagged as follow-up)
priority: low
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: register-repo-test-t5b-post-fr11-cleanup -->

**Original score:** 4.0 | **Recurrence:** 1

## Idea

T-17 added FR-11 to `config/scripts/register-repo.sh`: silent-failure step_history rows (agent != null/inline, status = completed, total_tokens IS NULL) are now rejected with a stderr warning. The pre-existing `config/scripts/__tests__/register-repo.test.sh` T-5b subtest has 2 assertions that still test the old buggy behavior:
- Expected empty stderr (now correctly emits a warning)
- Expected a row with NULL numerics (now correctly dropped to 0 rows)

## Scope

Trivial: update the 2 assertions to match FR-11 behavior. Update the test fixture comment to cite FR-11 (in `config/steps/contracts/metrics-schema.md` and the backlog entry for this feature).

## Scope estimate

~5 lines. Quick chore.

## Source

- single-source-metrics-via-step-events T-17 dev notes (declined to modify test outside allowed touch-set — correctly flagged as follow-up)

---
<!-- SECTION:DESCRIPTION:END -->
