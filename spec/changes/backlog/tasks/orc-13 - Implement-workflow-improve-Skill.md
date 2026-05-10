---
id: ORC-13
title: >-
  Implement /workflow-improve Skill (+ harden dispatch loop + fix missing
  contracts)
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-10 10:43'
labels:
  - slug-workflow-improve-skill-implementation
  - feature
  - score-7.5
  - recurrence-1
dependencies: []
priority: medium
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: workflow-improve-skill-implementation -->

**Original score:** 7.5 | **Recurrence:** 1

## Idea

The `/workflow-improve` skill at `skills/workflow-improve/SKILL.md` is a 3-line stub with no real logic ("Analyze metrics and identify improvements to workflow infrastructure"). Similarly, the `/telemetry` skill is a 2-line stub. The `/workflow-improve` skill should be the user-facing command that validates the full workflow graph: checks every schema's step references resolve to actual step contract YAMLs, checks every step contract's `agent:` field resolves to an agent `.md`, checks `flags_read` references exist in schema `defaults`, and validates template references. This overlaps with the existing `doctor-deep-check` backlog item but is runtime-invocable rather than a Makefile target, and focuses on structural integrity of the workflow graph rather than symlink health.

## Why Now

The orchestrator has 38 step contracts, 6 schemas, and 11 agents. As this grows, silent reference breakage (a schema referencing a step that was renamed, a step referencing an agent that was deleted) will become a real maintenance burden. The recent refactoring wave (renaming SPEC_CHANGES_DIR, moving config paths) is exactly the kind of change that creates these breakages.

## Priority

- User value: 7/10
- Strategic fit: 9/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 7.5**

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Validate all schema step refs resolve to actual step contract YAMLs; missing contracts pre-filtered with warning (absorbs ORC-28)
- [ ] #2 Validate all step contract agent: fields resolve to agent .md files
- [ ] #3 Validate flags_read references exist in schema defaults; validate template references
- [ ] #4 Add file-not-found guards to dispatch loop before each READ (step contract, agent def); add state.yaml write-after-verify pattern (absorbs ORC-14)
- [ ] #5 Surface validation results as user-facing /workflow-improve CLI command (runtime-invocable, not Makefile target)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Absorbed ORC-28 (fix missing step contracts ISSUE-18) and ORC-14 (harden dispatch loop) — all three address workflow graph integrity and dispatch robustness from the same surface area. ORC-28 pre-filters missing contracts at workflow-init; ORC-14 adds guards mid-dispatch; ORC-13 makes this user-invokable as /workflow-improve.
<!-- SECTION:NOTES:END -->
