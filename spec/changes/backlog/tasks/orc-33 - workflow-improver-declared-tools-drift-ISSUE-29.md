---
id: ORC-33
title: workflow-improver declared-tools drift (ISSUE-29)
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-workflow-improver-tools-frontmatter
  - bug
  - score-3.0
  - recurrence-1
dependencies: []
priority: low
ordinal: 32000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: workflow-improver-tools-frontmatter -->

**Original score:** 3.0 | **Recurrence:** 1

## Idea

`orchestrator cost --change-id` surfaces an `## Anomalies` section when
an agent uses a tool outside its declared frontmatter. During
autopilot-2026-04-20-001, workflow-improver used the `advisor` tool
during run-learn-cycle — not in its declared tools list. Either:

1. `advisor` is a legitimate capability for workflow-improver (it's
   making judgment calls under the classifier rules anyway) → add it
   to the frontmatter `tools:` array.
2. It shouldn't be reaching for `advisor` during routine learn cycles →
   scrub prompt examples / enforce via prompt.

Decide which, apply the one-line edit.

## Source

spec/changes/archive/2026-04-19-fix-inline-scripts-tmpdir/retro.md §ISSUE-29

---
<!-- SECTION:DESCRIPTION:END -->
