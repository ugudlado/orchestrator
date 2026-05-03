---
id: ORC-17
title: Audit and Prune Stub Skills
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-skill-stub-audit
  - feature
  - score-6.3
  - recurrence-1
dependencies: []
priority: low
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: skill-stub-audit -->

**Original score:** 6.3 | **Recurrence:** 1

## Idea

Several skills are effectively stubs with no real implementation: `/telemetry` (2 lines), `/workflow-improve` (3 lines), `/reflect` (likely minimal). Meanwhile, `/specify`, `/implement`, `/commit-group`, `/critique`, `/humanizer`, `/pal`, `/portless`, `/shadcn`, `/systematic-debugging`, and `/frontend-design` exist as skill directories. Some of these may be fully implemented, some may be stubs, and some may be dead code from earlier iterations. Audit all 22 skill directories: classify each as (a) fully implemented, (b) stub needing implementation, (c) dead code to remove, or (d) alias to another skill (like `/develop` -> `/orchestrate`). Then either implement the stubs that have clear value or remove the ones that are just noise. Having stub skills that users can invoke but that produce no useful output is worse than not having them at all.

## Why Now

The orchestrator is positioning itself as a universal workflow engine. Users discovering skills that do nothing will lose trust. Better to have 10 solid skills than 22 where half are empty.

## Priority

- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 6.3**

---
<!-- SECTION:DESCRIPTION:END -->
