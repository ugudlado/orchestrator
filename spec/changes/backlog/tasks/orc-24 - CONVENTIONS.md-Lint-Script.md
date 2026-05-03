---
id: ORC-24
title: CONVENTIONS.md Lint Script
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-conventions-lint-script
  - feature
  - score-3.0
  - recurrence-1
dependencies: []
priority: low
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: conventions-lint-script -->

**Original score:** 3.0 | **Recurrence:** 1

## Idea

Create a `scripts/lint-conventions.sh` that validates all step contracts, schemas, and templates against the format contracts defined in CONVENTIONS.md. Currently, CONVENTIONS.md defines detailed structural contracts (Task Format, Discovery Brief Format, Specification Format, Design Format, etc.) but compliance is only checked by the reviewer agent at runtime -- which means malformed artifacts waste an entire review cycle before being caught. A lint script could check: (1) every step contract has the 4 required sections (rules, instruction, verify, outputs), (2) every step intent is a single sentence, (3) `flags_read` entries have `effect` descriptions, (4) templates match their format contract sections.

## Why Now

CONVENTIONS.md is 1200+ lines and growing -- it's the richest source of structural rules in the project. The `/learn` skill actively adds rules to step contracts, and the `/workflow-improve` skill edits them. Neither has a way to verify they haven't introduced a structural violation. Adding a lint script to `make doctor` or as a pre-commit check would catch drift early.

## Prototype

```
$ bash scripts/lint-conventions.sh
Checking 38 step contracts...
  [OK] execute-next-task.yaml: 4 sections, single-sentence intent
  [OK] run-phase-review.yaml: 4 sections, single-sentence intent
  [FAIL] explore.yaml: missing outputs section (found: discovery_result)
  [WARN] diagnose.yaml: intent uses "and" — possible SRP violation
Checking 6 schemas...
  [OK] feature.yaml: all step refs resolve
Checking 9 templates...
  [OK] feature/spec.md: matches Specification Format Contract sections
Summary: 47 files checked, 1 error, 1 warning
```

## Priority

- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 3.0**

---
<!-- SECTION:DESCRIPTION:END -->
