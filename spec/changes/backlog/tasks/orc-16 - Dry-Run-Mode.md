---
id: ORC-16
title: Dry Run Mode
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-dry-run-mode
  - feature
  - score-6.7
  - recurrence-1
dependencies: []
priority: low
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: dry-run-mode -->

**Original score:** 6.7 | **Recurrence:** 1

## Idea

Add a `--dry-run` flag to all schemas that prints the resolved step plan without executing anything. Output would show: schema selected, flags resolved, each phase with its filtered steps (marking conditional steps with their condition), and which agents would be spawned. This gives users a preview of what `/develop` will do before it starts creating worktrees, spawning agents, and modifying state.

## Why Now

The orchestrate skill already does schema resolution and step filtering (SKILL.md sections 1 and 3). A dry run just stops before section 4 (dispatch loop). Users currently have to read schema YAML files manually to understand what steps will run -- the conditional `if` / `if not` filtering makes this non-trivial. This is especially useful when testing new flag combinations or debugging why a step was skipped.

## Prototype

Before/after example:

```
$ /develop "add search feature" --no-tdd --no-design --dry-run

Schema: feature (v3)
Flags: tdd_required=false, design=false, ux_design=true, auto=false, linear=true

Phase: specify
  1. create-worktree
  2. load-project-context
  3. explore                        [agent: discoverer]
  4. design-exploration              SKIPPED (design=false)
  5. ux-design                      [agent: ux-reviewer]
  6. create-or-refresh-artifacts    [agent: architect]
  7. run-phase-review               [agent: reviewer]
  8. create-linear-ticket
  9. phase-signoff

Phase: implement
  1. execute-next-task (repeat)     [agent: developer]
  2. run-simplify                   [agent: developer]
  3. run-ux-critique                [agent: ux-reviewer]
  4. run-phase-review               [agent: reviewer]
  5. final-signoff

Phase: complete
  ...
```

## Priority

- User value: 7/10
- Strategic fit: 6/10
- Technical leverage: 7/10
- Effort: small
- **Score: 6.7**

---
<!-- SECTION:DESCRIPTION:END -->
